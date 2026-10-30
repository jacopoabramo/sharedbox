import asyncio
import multiprocessing as mp
import threading
import time
from multiprocessing.queues import Queue
from typing import Any

import pytest
from streamproc import finish, read_metadata_versions

from sharedbox import (
    MetadataFullError,
    MetadataGoneError,
    SharedStream,
    StreamClosedError,
    WouldBlock,
)

try:
    import event_model

    HAS_EVENT_MODEL = True
except ImportError:
    HAS_EVENT_MODEL = False

CONTEXT = mp.get_context("spawn")


def test_a_reader_sees_the_version_of_each_item(unique_name: str) -> None:
    """Report for each item received the newest metadata version added before it was sent."""
    with SharedStream.create(
        int, unique_name, capacity=8, metadata={"step": 1}
    ) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender()
        assert (reader.metadata, reader.metadata_version) == (None, 0)
        versions = []
        for item, step in ((0, None), (1, None), (2, 2), (3, 3)):
            if step is None:
                sender.send(item)
            else:
                sender.send(item, metadata={"step": step})
            assert reader.receive(timeout=5) == item
            versions.append(reader.metadata_version)
        assert versions == [1, 1, 2, 3]
        assert reader.metadata == {"step": 3}
    SharedStream.unlink(unique_name)


def test_a_reader_behind_sees_the_old_version(unique_name: str) -> None:
    """Report the version an item was sent under, though newer ones were added before it was received."""
    with SharedStream.create(
        int, unique_name, capacity=8, metadata={"step": 1}
    ) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender()
        sender.send(0)
        sender.send(1)
        sender.send(2, metadata={"step": 2})
        seen = []
        for _ in range(3):
            reader.receive(timeout=5)
            seen.append((reader.metadata_version, reader.metadata))
        assert seen == [(1, {"step": 1}), (1, {"step": 1}), (2, {"step": 2})]
        assert (stream.metadata_version, stream.metadata) == (2, {"step": 2})
        assert stream.metadata_version_of(1) == {"step": 1}
    SharedStream.unlink(unique_name)


def test_a_send_with_metadata_applies_to_its_own_item(unique_name: str) -> None:
    """Give the item sent with metadata the new version and the item before it the old one."""
    with SharedStream.create(
        int, unique_name, capacity=8, metadata={"step": 1}
    ) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender()
        sender.send(0)
        sender.send(1, metadata={"step": 2})
        sender.send(2)
        versions = []
        for _ in range(3):
            reader.receive(timeout=5)
            versions.append(reader.metadata_version)
        assert versions == [1, 2, 2]
        assert reader.metadata == {"step": 2}
    SharedStream.unlink(unique_name)


def test_an_empty_document_adds_a_version(unique_name: str) -> None:
    """Add a version for metadata={}, which is a document, not None."""
    with SharedStream.create(
        int, unique_name, capacity=8, metadata={"step": 1}
    ) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender()
        sender.send(0, metadata={})
        assert reader.receive(timeout=5) == 0
        assert (reader.metadata_version, reader.metadata) == (2, {})
    SharedStream.unlink(unique_name)


def test_a_bad_document_sends_nothing_even_on_a_full_ring(unique_name: str) -> None:
    """Raise TypeError for a document that cannot be stored, before WouldBlock, and send nothing."""
    with SharedStream.create(
        int, unique_name, capacity=2, metadata={"step": 1}
    ) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender()
        sender.send_nowait(0)
        sender.send_nowait(1)
        with pytest.raises(WouldBlock):
            sender.send_nowait(9)
        with pytest.raises(TypeError):
            sender.send_nowait(9, metadata={"a": object()})
        with pytest.raises(ValueError, match="metadata_capacity"):
            sender.send_nowait(9, metadata={"text": "x" * 30000})
        assert (stream.statistics().sent, stream.metadata_version) == (2, 1)
        assert [reader.receive(timeout=5) for _ in range(2)] == [0, 1]
    SharedStream.unlink(unique_name)


def test_an_item_that_cannot_be_encoded_adds_no_version(unique_name: str) -> None:
    """Add no version when the item itself is refused."""
    with SharedStream.create(
        int, unique_name, capacity=8, metadata={"step": 1}
    ) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender()
        with pytest.raises(TypeError):
            sender.send("x", metadata={"step": 2})  # type: ignore[arg-type]
        assert stream.metadata_version == 1
        sender.send(1)
        assert reader.receive(timeout=5) == 1
        assert reader.metadata_version == 1
    SharedStream.unlink(unique_name)


def test_a_refused_add_releases_no_reader(unique_name: str) -> None:
    """Refuse an add that would reclaim a needed version without waiting or releasing a lossless reader."""
    with SharedStream.create(
        int, unique_name, capacity=4, metadata={"step": 1}, metadata_versions=2
    ) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender(on_full="release")
        sender.send(0)
        sender.send(1, metadata={"step": 2})
        sender.send(2)
        sender.send(3)
        start = time.monotonic()
        with pytest.raises(MetadataFullError):
            sender.send(4, timeout=5, metadata={"step": 3})
        assert time.monotonic() - start < 1
        assert [reader.receive(timeout=5) for _ in range(4)] == [0, 1, 2, 3]
        assert stream.metadata_version == 2
    SharedStream.unlink(unique_name)


def test_a_send_that_waited_for_a_slot_holder_is_refused_without_release(
    unique_name: str,
) -> None:
    """Refuse a send with metadata once another thread's slot is sent, before the ring wait or a release."""
    timeout = 5.0
    outcome: list[tuple[BaseException | None, float]] = []

    def send_with_metadata() -> None:
        start = time.monotonic()
        try:
            sender.send(9, timeout=timeout, metadata={"step": 2})
        except BaseException as error:
            outcome.append((error, time.monotonic() - start))

    with SharedStream.create(int, unique_name, capacity=2) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender(on_full="release")
        sender.send(0)
        with sender.writing(0) as slot:
            thread = threading.Thread(target=send_with_metadata)
            thread.start()
            time.sleep(0.2)
            slot.send(1)
        thread.join()
        error, elapsed = outcome[0]
        assert isinstance(error, MetadataFullError)
        # Without the refusal on waking, the send waits for the ring to the end of its one-second step.
        assert elapsed < 0.9
        assert [reader.receive(timeout=5) for _ in range(2)] == [0, 1]
        with pytest.raises(WouldBlock):
            reader.receive_nowait()
    SharedStream.unlink(unique_name)


def test_a_slot_send_with_metadata(unique_name: str) -> None:
    """Add the version with the item sent from a writing block, and keep the slot usable after a refused document."""
    with SharedStream.create(
        int, unique_name, capacity=4, metadata={"step": 1}
    ) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender()
        with sender.writing(0) as slot:
            with pytest.raises(TypeError):
                slot.send(5, metadata={"a": object()})
            slot.send(5, metadata={"step": 2})
        assert reader.receive(timeout=5) == 5
        assert (reader.metadata_version, reader.metadata) == (2, {"step": 2})
    SharedStream.unlink(unique_name)


def test_a_refused_slot_send_leaves_the_slot_to_send_again(unique_name: str) -> None:
    """Refuse slot.send with metadata on a stream without a metadata area, and publish on a later slot.send."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader("lossless")
        with stream.sender().writing(0) as slot:
            with pytest.raises(MetadataFullError):
                slot.send(5, metadata={})
            slot.send(5)
        assert reader.receive(timeout=5) == 5
    SharedStream.unlink(unique_name)


def test_asend_with_metadata(unique_name: str) -> None:
    """Add the version with an asend, and none when the asend times out on a full ring."""

    async def run() -> None:
        await sender.asend(1, metadata={"step": 2})
        assert reader.receive(timeout=5) == 1
        assert reader.metadata_version == 2
        for item in range(2, 6):
            await sender.asend(item)
        with pytest.raises(TimeoutError):
            await sender.asend(9, timeout=0.1, metadata={"step": 3})

    with SharedStream.create(
        int, unique_name, capacity=4, metadata={"step": 1}
    ) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender()
        asyncio.run(run())
        assert (stream.statistics().sent, stream.metadata_version) == (5, 2)
    SharedStream.unlink(unique_name)


def test_versions_across_processes(unique_name: str) -> None:
    """Report in another process the version and metadata each item was sent under."""
    with SharedStream.create(
        int, unique_name, capacity=8, metadata={"step": 1}
    ) as stream:
        sender = stream.sender()
        sender.send(0)
        sender.send(1)
        sender.send(2, metadata={"step": 2})
        sender.send(3, metadata={"step": 3})
        results: Queue[list[tuple[int, int, dict[str, Any] | None]] | str] = (
            CONTEXT.Queue()
        )
        child = CONTEXT.Process(
            target=read_metadata_versions, args=(unique_name, 4, results)
        )
        child.start()
        seen = results.get(timeout=60)
        finish(child)
    assert seen == [
        (0, 1, {"step": 1}),
        (1, 1, {"step": 1}),
        (2, 2, {"step": 2}),
        (3, 3, {"step": 3}),
    ]
    SharedStream.unlink(unique_name)


def test_a_send_with_metadata_is_refused_while_an_item_needs_the_version(
    unique_name: str,
) -> None:
    """Refuse a send with a third version while an item sent under the first is in the ring, and take it once that item is overwritten."""
    with SharedStream.create(
        int, unique_name, capacity=4, metadata={"step": 1}, metadata_versions=2
    ) as stream:
        sender = stream.sender()
        sender.send(0)
        sender.send(1, metadata={"step": 2})
        for item in range(2, 5):
            with pytest.raises(MetadataFullError, match="metadata_capacity"):
                sender.send(item, metadata={"step": 3})
            sender.send(item)
        assert (stream.statistics().sent, stream.metadata_version) == (5, 2)
        sender.send(5, metadata={"step": 3})
        assert (stream.statistics().sent, stream.metadata_version) == (6, 3)
    SharedStream.unlink(unique_name)


def test_a_lossy_reader_whose_version_is_gone(unique_name: str) -> None:
    """Raise MetadataGoneError for a lossy reader whose item's version was reclaimed after it read it."""
    with SharedStream.create(
        int, unique_name, capacity=4, metadata={"step": 1}, metadata_versions=2
    ) as stream:
        sender = stream.sender()
        for item in range(3):
            sender.send(item)
        reader = stream.reader("lossy", "oldest")
        for _ in range(3):
            reader.receive(timeout=5)
        sender.send(3, metadata={"step": 2})
        assert reader.metadata == {"step": 1}
        for item in range(4, 7):
            sender.send(item)
        sender.send(7, metadata={"step": 3})
        assert stream.metadata_version == 3
        with pytest.raises(MetadataGoneError):
            _ = reader.metadata
    SharedStream.unlink(unique_name)


def test_a_send_with_metadata_on_a_closed_sender(unique_name: str) -> None:
    """Raise StreamClosedError for a send with metadata on a closed sender, and add nothing."""
    with SharedStream.create(
        int, unique_name, capacity=4, metadata={"step": 1}
    ) as stream:
        sender = stream.sender()
        sender.close()
        with pytest.raises(StreamClosedError):
            sender.send(1, metadata={"step": 2})
        assert stream.metadata_version == 1
    SharedStream.unlink(unique_name)


def test_metadata_of_a_closed_reader(unique_name: str) -> None:
    """Raise StreamClosedError for the metadata of a closed reader that never received an item."""
    with SharedStream.create(
        int, unique_name, capacity=4, metadata={"step": 1}
    ) as stream:
        reader = stream.reader()
        reader.close()
        with pytest.raises(StreamClosedError):
            _ = reader.metadata_version
        with pytest.raises(StreamClosedError):
            _ = reader.metadata
    SharedStream.unlink(unique_name)


def test_items_sent_before_the_first_version_have_none(unique_name: str) -> None:
    """Report no metadata for an item sent before the first version was added."""
    with SharedStream.create(
        int, unique_name, capacity=4, metadata_capacity=1024
    ) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender()
        sender.send(0)
        sender.send(1, metadata={"step": 1})
        seen = []
        for _ in range(2):
            reader.receive(timeout=5)
            seen.append((reader.metadata_version, reader.metadata))
        assert seen == [(0, None), (1, {"step": 1})]
    SharedStream.unlink(unique_name)


def test_the_first_version_is_kept_for_items_sent_before_it(unique_name: str) -> None:
    """Refuse to reclaim version 1 while an item sent before it is in the ring, and report no metadata for that item."""
    with SharedStream.create(
        int, unique_name, capacity=8, metadata_capacity=1024, metadata_versions=2
    ) as stream:
        sender = stream.sender()
        reader = stream.reader(start="oldest")
        sender.send(0)
        sender.send(1, metadata={"v": 1})
        sender.send(2, metadata={"v": 2})
        with pytest.raises(MetadataFullError):
            sender.send(3, metadata={"v": 3})
        assert stream.metadata_version == 2
        assert reader.receive(timeout=5) == 0
        assert (reader.metadata_version, reader.metadata) == (0, None)
    SharedStream.unlink(unique_name)


def test_a_stream_without_metadata(unique_name: str) -> None:
    """Read no metadata from a stream created without it, and refuse to send any."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender()
        sender.send(0)
        reader.receive(timeout=5)
        assert (reader.metadata, reader.metadata_version) == (None, 0)
        assert (stream.metadata, stream.metadata_version) == (None, 0)
        with pytest.raises(MetadataFullError, match="metadata_capacity"):
            sender.send(1, metadata={"step": 1})
        with pytest.raises(MetadataGoneError):
            stream.metadata_version_of(1)
    SharedStream.unlink(unique_name)


def test_a_cached_stream_version_is_gone_once_reclaimed(unique_name: str) -> None:
    """Raise MetadataGoneError for a reclaimed version this stream object read before."""
    with SharedStream.create(
        int, unique_name, capacity=4, metadata={"step": 1}, metadata_versions=2
    ) as stream:
        sender = stream.sender()
        assert stream.metadata_version_of(1) == {"step": 1}
        for step in (2, 3):
            sender.send(step, metadata={"step": step})
        with pytest.raises(MetadataGoneError):
            stream.metadata_version_of(1)
        assert stream.metadata == {"step": 3}
    SharedStream.unlink(unique_name)


@pytest.mark.parametrize(
    ("metadata", "keywords", "message"),
    [
        (
            {"text": "x" * 100},
            {"metadata_capacity": 64},
            "capacity of 64 takes at most",
        ),
        ({"step": 1}, {"metadata_versions": 1}, "metadata_versions 1 does not make"),
        ({"step": 1}, {"metadata_capacity": 0}, "metadata_capacity 0 with"),
    ],
)
def test_metadata_that_does_not_fit_is_refused(
    unique_name: str, metadata: dict[str, Any], keywords: dict[str, Any], message: str
) -> None:
    """Raise ValueError naming metadata_capacity for metadata the stream's area cannot hold, and create nothing."""
    with pytest.raises(ValueError, match=message):
        SharedStream.create(int, unique_name, capacity=4, metadata=metadata, **keywords)
    SharedStream.create(int, unique_name, capacity=4).close()
    SharedStream.unlink(unique_name)


@pytest.mark.skipif(not HAS_EVENT_MODEL, reason="event-model is not installed")
def test_event_model_descriptors(unique_name: str) -> None:
    """Rebuild events that name the descriptor in force when each reading was sent, across a configuration change."""
    run = event_model.compose_run()
    data_keys = {"x": {"source": "sim", "dtype": "number", "shape": []}}
    descriptors = [
        run.compose_descriptor(
            data_keys=data_keys,
            name="primary",
            configuration={
                "det": {
                    "data": {"gain": gain},
                    "timestamps": {"gain": 0.0},
                    "data_keys": {
                        "gain": {"source": "sim", "dtype": "integer", "shape": []}
                    },
                }
            },
        )
        for gain in (1, 2)
    ]
    compose = {d.descriptor_doc["uid"]: d.compose_event for d in descriptors}
    first, second = (d.descriptor_doc["uid"] for d in descriptors)
    with SharedStream.create(
        float, unique_name, capacity=8, metadata=descriptors[0].descriptor_doc
    ) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender()
        sender.send(0.5)
        sender.send(1.5)
        sender.send(2.5, metadata=descriptors[1].descriptor_doc)
        sender.send(3.5)
        documents: list[tuple[str, str]] = []
        version = 0
        for _ in range(4):
            x = reader.receive(timeout=5)
            descriptor = reader.metadata
            assert descriptor is not None
            if reader.metadata_version != version:
                version = reader.metadata_version
                documents.append(("descriptor", descriptor["uid"]))
            event = compose[descriptor["uid"]](data={"x": x}, timestamps={"x": 0.0})
            documents.append(("event", event["descriptor"]))
    assert documents == [
        ("descriptor", first),
        ("event", first),
        ("event", first),
        ("descriptor", second),
        ("event", second),
        ("event", second),
    ]
    SharedStream.unlink(unique_name)
