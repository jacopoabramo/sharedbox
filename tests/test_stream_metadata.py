import multiprocessing as mp
from multiprocessing.queues import Queue
from typing import Any

import pytest
from streamproc import finish, read_metadata_versions

from sharedbox import (
    MetadataFullError,
    MetadataGoneError,
    SharedStream,
    StreamClosedError,
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
        for item, adds in ((0, []), (1, []), (2, [2]), (3, [3, 4])):
            for step in adds:
                assert sender.add_metadata({"step": step}) == step
            sender.send(item)
            assert reader.receive(timeout=5) == item
            versions.append(reader.metadata_version)
        assert versions == [1, 1, 2, 4]
        assert reader.metadata == {"step": 4}
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
        sender.add_metadata({"step": 2})
        sender.send(2)
        seen = []
        for _ in range(3):
            reader.receive(timeout=5)
            seen.append((reader.metadata_version, reader.metadata))
        assert seen == [(1, {"step": 1}), (1, {"step": 1}), (2, {"step": 2})]
        assert (stream.metadata_version, stream.metadata) == (2, {"step": 2})
        assert stream.metadata_version_of(1) == {"step": 1}
    SharedStream.unlink(unique_name)


def test_versions_across_processes(unique_name: str) -> None:
    """Report in another process the version and metadata each item was sent under."""
    with SharedStream.create(
        int, unique_name, capacity=8, metadata={"step": 1}
    ) as stream:
        sender = stream.sender()
        sender.send(0)
        sender.send(1)
        sender.add_metadata({"step": 2})
        sender.send(2)
        sender.add_metadata({"step": 3})
        sender.send(3)
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


def test_add_metadata_full_while_an_item_needs_it(unique_name: str) -> None:
    """Refuse a third version while an item sent under the first is in the ring, and take it once that item is overwritten."""
    with SharedStream.create(
        int, unique_name, capacity=4, metadata={"step": 1}, metadata_versions=2
    ) as stream:
        sender = stream.sender()
        sender.send(0)
        assert sender.add_metadata({"step": 2}) == 2
        for item in range(1, 5):
            with pytest.raises(MetadataFullError, match="metadata_capacity"):
                sender.add_metadata({"step": 3})
            sender.send(item)
        assert sender.add_metadata({"step": 3}) == 3
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
        sender.add_metadata({"step": 2})
        assert reader.metadata == {"step": 1}
        for item in range(3, 7):
            sender.send(item)
        assert sender.add_metadata({"step": 3}) == 3
        with pytest.raises(MetadataGoneError):
            _ = reader.metadata
    SharedStream.unlink(unique_name)


def test_add_metadata_on_a_closed_sender(unique_name: str) -> None:
    """Raise StreamClosedError for adding metadata on a closed sender, and add nothing."""
    with SharedStream.create(
        int, unique_name, capacity=4, metadata={"step": 1}
    ) as stream:
        sender = stream.sender()
        sender.close()
        with pytest.raises(StreamClosedError):
            sender.add_metadata({"step": 2})
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
        assert sender.add_metadata({"step": 1}) == 1
        sender.send(1)
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
        sender.send(1)
        assert sender.add_metadata({"v": 1}) == 1
        assert sender.add_metadata({"v": 2}) == 2
        with pytest.raises(MetadataFullError):
            sender.add_metadata({"v": 3})
        assert reader.receive(timeout=5) == 0
        assert (reader.metadata_version, reader.metadata) == (0, None)
    SharedStream.unlink(unique_name)


def test_a_stream_without_metadata(unique_name: str) -> None:
    """Read no metadata from a stream created without it, and refuse to add any."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender()
        sender.send(0)
        reader.receive(timeout=5)
        assert (reader.metadata, reader.metadata_version) == (None, 0)
        assert (stream.metadata, stream.metadata_version) == (None, 0)
        with pytest.raises(MetadataFullError, match="metadata_capacity"):
            sender.add_metadata({"step": 1})
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
            sender.add_metadata({"step": step})
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
        sender.add_metadata(descriptors[1].descriptor_doc)
        sender.send(2.5)
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
