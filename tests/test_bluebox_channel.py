import multiprocessing as mp
import threading
import time
from collections.abc import Sequence
from typing import Any

import cbor2
import event_model
import numpy as np
import pytest
from bluebox_compare import assert_documents_equal
from blueboxproc import Child, collect, start_run_and_exit

from sharedbox import EndOfStream, SegmentExistsError, SharedStream
from sharedbox.bluebox import (
    DocumentReceiver,
    DocumentSender,
    LateJoinError,
    ReceiverReleasedError,
)
from sharedbox.bluebox._channel import ChannelState

Document = tuple[str, dict[str, Any]]
CONTEXT = mp.get_context("spawn")
NUMBER = {"source": "sim://number", "dtype": "number", "shape": []}


def run_documents(
    metadata: dict[str, Any] | None = None,
    resource_parameters: dict[str, Any] | None = None,
) -> list[Document]:
    """Return one run: start, two descriptors, ten events, a page of 40 rows, a stream resource and datum, stop."""
    bundle = event_model.compose_run(metadata=metadata)
    primary = bundle.compose_descriptor(
        name="primary",
        data_keys={
            "motor": NUMBER,
            "spectrum": {
                "source": "sim://spectrum",
                "dtype": "array",
                "dtype_numpy": "<u2",
                "shape": [5],
            },
        },
    )
    baseline = bundle.compose_descriptor(
        name="baseline",
        data_keys={
            "temperature": NUMBER,
            "label": {"source": "sim://label", "dtype": "string", "shape": []},
        },
    )
    documents: list[Document] = [
        ("start", dict(bundle.start_doc)),
        ("descriptor", dict(primary.descriptor_doc)),
        ("descriptor", dict(baseline.descriptor_doc)),
    ]
    for index in range(10):
        event = primary.compose_event(
            data={
                "motor": index / 2,
                "spectrum": np.arange(5, dtype=np.uint16) + index,
            },
            timestamps={"motor": 100.0 + index, "spectrum": 100.0 + index},
            seq_num=index + 1,
        )
        documents.append(("event", dict(event)))
    rows = range(40)
    page = baseline.compose_event_page(
        data={
            "temperature": [20.0 + row / 8 for row in rows],
            "label": [f"row {row}" for row in rows],
        },
        timestamps={
            "temperature": [200.0 + row for row in rows],
            "label": [200.0 + row for row in rows],
        },
        seq_num=[row + 1 for row in rows],
        time=[300.0 + row for row in rows],
        uid=[f"page-{row}" for row in rows],
    )
    documents.append(("event_page", dict(page)))
    assert bundle.compose_stream_resource is not None
    resource = bundle.compose_stream_resource(
        mimetype="application/x-hdf5",
        uri="file://localhost/tmp/det.h5",
        data_key="det",
        parameters=resource_parameters or {"dataset": "/entry/data"},
    )
    datum = resource.compose_stream_datum(
        indices={"start": 0, "stop": 10}, descriptor=primary.descriptor_doc
    )
    documents += [
        ("stream_resource", dict(resource.stream_resource_doc)),
        ("stream_datum", dict(datum)),
        ("stop", dict(bundle.compose_stop())),
    ]
    return documents


def send_all(sender: DocumentSender, documents: Sequence[Document]) -> None:
    """Send each document in order."""
    for name, doc in documents:
        sender(name, doc)


class Collector:
    """A thread that receives documents until a stop, the end of the stream or an error.

    With `pause_after`, it stops receiving after that many documents until
    `resume` is set.
    """

    def __init__(
        self,
        receiver: DocumentReceiver,
        *,
        pause_after: int | None = None,
        until_stop: bool = True,
    ) -> None:
        self.receiver = receiver
        self.documents: list[Document] = []
        self.error: BaseException | None = None
        self.ended_at: float | None = None
        self.resume = threading.Event()
        self.pause_after = pause_after
        self.until_stop = until_stop
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def run(self) -> None:
        try:
            while True:
                if len(self.documents) == self.pause_after:
                    self.resume.wait()
                name, doc = self.receiver.receive()
                self.documents.append((name, doc))
                if name == "stop" and self.until_stop:
                    return
        except EndOfStream:
            self.ended_at = time.monotonic()
        except Exception as error:
            self.error = error
            # A receiver left open would hold back a sender that waits for it.
            self.receiver.close()

    def join(self) -> list[Document]:
        self.resume.set()
        self.thread.join(10)
        assert not self.thread.is_alive(), "the receiver thread did not finish"
        if self.error is not None:
            raise self.error
        return self.documents


def test_documents_arrive_in_order(unique_name: str) -> None:
    """Deliver every document of a run to a lossless receiver in the order sent."""
    documents = run_documents()
    with (
        DocumentSender.create(unique_name) as sender,
        DocumentReceiver.attach(unique_name) as receiver,
    ):
        collector = Collector(receiver)
        send_all(sender, documents)

        assert_documents_equal(documents, collector.join())


def test_large_documents_pass_in_parts(unique_name: str) -> None:
    """Carry a descriptor of 500 keys and a start of 1 MiB of metadata unchanged."""
    bundle = event_model.compose_run(metadata={"note": "x" * (1024 * 1024)})
    keys = {f"signal{index}": NUMBER for index in range(500)}
    descriptor = bundle.compose_descriptor(name="primary", data_keys=keys)
    event = descriptor.compose_event(
        data={key: float(index) for index, key in enumerate(keys)},
        timestamps=dict.fromkeys(keys, 1.0),
        seq_num=1,
    )
    documents: list[Document] = [
        ("start", dict(bundle.start_doc)),
        ("descriptor", dict(descriptor.descriptor_doc)),
        ("event", dict(event)),
        ("stop", dict(bundle.compose_stop())),
    ]
    with (
        DocumentSender.create(unique_name) as sender,
        DocumentReceiver.attach(unique_name) as receiver,
    ):
        collector = Collector(receiver)
        send_all(sender, documents)

        assert_documents_equal(documents, collector.join())


@pytest.mark.parametrize("note", [0, 600 * 1024], ids=["small", "large"])
def test_late_receiver_gets_start_first(unique_name: str, note: int) -> None:
    """Give a receiver attached after five events the start and descriptors, then the events since the last descriptor."""
    documents = run_documents(metadata={"note": "x" * note})
    with DocumentSender.create(unique_name) as sender:
        send_all(sender, documents[:8])
        with DocumentReceiver.attach(unique_name) as receiver:
            collector = Collector(receiver)
            send_all(sender, documents[8:])

            # No control document was sent after the five events, and their
            # ring still holds them, so the receiver gets them too.
            assert_documents_equal(documents, collector.join())


def test_state_too_large_refuses_late_join(unique_name: str) -> None:
    """Run to the stop with a start the state cannot hold, and refuse a receiver attaching mid-run."""
    documents = run_documents(metadata={"note": "x" * (1024 * 1024)})
    with (
        DocumentSender.create(unique_name) as sender,
        DocumentReceiver.attach(unique_name) as receiver,
    ):
        collector = Collector(receiver)
        send_all(sender, documents[:5])

        with pytest.raises(LateJoinError):
            DocumentReceiver.attach(unique_name)

        send_all(sender, documents[5:])
        assert_documents_equal(documents, collector.join())


def test_unknown_document_name_travels(unique_name: str) -> None:
    """Deliver a document of a name bluebox does not know under that name."""
    with (
        DocumentSender.create(unique_name) as sender,
        DocumentReceiver.attach(unique_name) as receiver,
    ):
        sender("future_kind", {"x": 1})

        assert receiver.receive(timeout=5) == ("future_kind", {"x": 1})


def test_unseen_descriptor_is_refused(unique_name: str) -> None:
    """Refuse an event whose descriptor the sender has not sent."""
    documents = run_documents()
    with DocumentSender.create(unique_name) as sender:
        sender(*documents[0])

        with pytest.raises(ValueError, match="descriptor"):
            sender(*documents[3])


@pytest.mark.parametrize("held", ["start", "stream_resource"])
def test_receiver_never_starts_mid_document(unique_name: str, held: str) -> None:
    """Never deliver part of a document a receiver attached in the middle of."""
    big = "x" * (70 * 1024)
    if held == "start":
        documents = run_documents(metadata={"note": big})
        sent_before = 0
        # The start rides in the state, so the late receiver gets the whole run.
        expected_first = expected_late = list(range(17))
    else:
        documents = run_documents(resource_parameters={"note": big})
        sent_before = 14
        # Both receivers attach after the page. The first begins with the run's
        # state and the events its rings still hold, since no control document
        # was sent after the descriptors. The late one starts at a part of the
        # half-sent stream resource, so it drops that and every event before.
        expected_first = [0, 1, 2, *range(5, 17)]
        expected_late = [0, 1, 2, 15, 16]
    with DocumentSender.create(unique_name, control_capacity=4) as sender:
        send_all(sender, documents[:sent_before])
        with DocumentReceiver.attach(unique_name) as first:
            collector = Collector(first, pause_after=0)
            sending = threading.Thread(
                target=send_all, args=(sender, documents[sent_before:])
            )
            sending.start()
            # The first receiver does not read, so the sender waits with four
            # parts of the held document in the ring.
            time.sleep(0.5)
            with DocumentReceiver.attach(unique_name) as late:
                late_collector = Collector(late)
                collector.resume.set()
                sending.join(10)
                first_documents = collector.join()
                late_documents = late_collector.join()

    assert_documents_equal([documents[i] for i in expected_first], first_documents)
    assert_documents_equal([documents[i] for i in expected_late], late_documents)


def test_late_lossless_receiver_is_released(unique_name: str) -> None:
    """Release a lossless receiver that opens an event stream after its events were overwritten."""
    documents = run_documents()
    with (
        DocumentSender.create(
            unique_name, event_capacity=2, control_timeout=0.2
        ) as sender,
        DocumentReceiver.attach(unique_name) as receiver,
    ):
        send_all(sender, documents)

        with pytest.raises(ReceiverReleasedError):
            for _ in documents:
                receiver.receive(timeout=5)


def test_stuck_receiver_is_released(unique_name: str) -> None:
    """Release a receiver that never reads once, and let the run reach its stop without waiting for it again."""
    documents = run_documents()
    with (
        DocumentSender.create(
            unique_name, control_capacity=2, control_timeout=0.5
        ) as sender,
        DocumentReceiver.attach(unique_name) as receiver,
    ):
        began = time.monotonic()
        send_all(sender, documents)
        elapsed = time.monotonic() - began

        # One control_timeout for the release; waiting for the released
        # receiver at each of the two event streams would add two more.
        assert elapsed < 1.0
        with pytest.raises(ReceiverReleasedError):
            receiver.receive(timeout=5)


def test_killed_receiver_is_not_waited_for(unique_name: str) -> None:
    """Send three runs at full speed after a lossless receiver's process was killed."""
    with DocumentSender.create(unique_name, control_timeout=0.3) as sender:
        with Child(collect, name=unique_name, mode="lossless") as child:
            child.kill()
            child.result()
        began = time.monotonic()
        for _ in range(3):
            send_all(sender, run_documents())
        elapsed = time.monotonic() - began

    # Each run has two event streams; waiting at each would take 1.8 s.
    assert elapsed < 0.5


def test_stuck_receiver_holds_each_run_back_once(unique_name: str) -> None:
    """Wait at most control_timeout per run for a lossless receiver that never reads."""
    with (
        DocumentSender.create(unique_name, control_timeout=0.3) as sender,
        DocumentReceiver.attach(unique_name),
    ):
        began = time.monotonic()
        for _ in range(3):
            send_all(sender, run_documents())
        elapsed = time.monotonic() - began

    # 0.9 s at most; waiting at each of the two event streams would take 1.8 s.
    assert elapsed < 1.3


def numbered_run(events: int) -> list[Document]:
    """Return a run of one descriptor with `events` events."""
    bundle = event_model.compose_run()
    primary = bundle.compose_descriptor(name="primary", data_keys={"x": NUMBER})
    return [
        ("start", dict(bundle.start_doc)),
        ("descriptor", dict(primary.descriptor_doc)),
        *(
            (
                "event",
                dict(
                    primary.compose_event(
                        data={"x": float(index)}, timestamps={"x": 1.0}
                    )
                ),
            )
            for index in range(events)
        ),
        ("stop", dict(bundle.compose_stop())),
    ]


def test_slow_lossless_receiver_keeps_up_over_runs(unique_name: str) -> None:
    """Deliver three runs to a lossless receiver that takes 0.1 s per event, with control_timeout 0.3."""
    runs = [numbered_run(12) for _ in range(3)]
    with (
        DocumentSender.create(unique_name, control_timeout=0.3) as sender,
        Child(collect, name=unique_name, mode="lossless", delay=0.1, stops=3) as child,
    ):
        for documents in runs:
            send_all(sender, documents)
        received, ending = child.result()

    assert ending == "done"
    assert_documents_equal([pair for run in runs for pair in run], received)


def test_lossless_receiver_keeps_a_run_without_events(unique_name: str) -> None:
    """Deliver a run whose descriptor had no events to a lossless receiver that reads after the stop."""
    documents = numbered_run(0)
    with (
        DocumentSender.create(unique_name, control_timeout=0.2) as sender,
        DocumentReceiver.attach(unique_name) as receiver,
    ):
        send_all(sender, documents)

        assert_documents_equal(
            documents, [receiver.receive(timeout=5) for _ in documents]
        )


def test_event_send_releases_a_slow_lossless_receiver(unique_name: str) -> None:
    """Release a lossless receiver that stops reading events, with on_full="release"."""
    documents = run_documents()
    with (
        DocumentSender.create(
            unique_name, on_full="release", event_capacity=2, event_timeout=0.2
        ) as sender,
        DocumentReceiver.attach(unique_name) as receiver,
    ):
        send_all(sender, documents[:3])
        for _ in range(3):
            receiver.receive(timeout=5)
        send_all(sender, documents[3:])

        # The receiver delivers what it took before the release, then raises.
        with pytest.raises(ReceiverReleasedError):
            for _ in documents:
                receiver.receive(timeout=5)


def test_event_send_times_out_on_a_slow_lossless_receiver(unique_name: str) -> None:
    """Raise TimeoutError from an event send a lossless receiver holds back, with on_full="wait"."""
    documents = run_documents()
    with (
        DocumentSender.create(
            unique_name, on_full="wait", event_capacity=2, event_timeout=0.2
        ) as sender,
        DocumentReceiver.attach(unique_name) as receiver,
    ):
        send_all(sender, documents[:3])
        for _ in range(3):
            receiver.receive(timeout=5)

        with pytest.raises(TimeoutError):
            send_all(sender, documents[3:13])


def test_lossy_receiver_does_not_hold_back_events(unique_name: str) -> None:
    """Send a run at full speed past a lossy receiver that never reads, and lose nothing for a lossless one."""
    documents = run_documents()
    with (
        DocumentSender.create(
            unique_name, event_capacity=2, control_timeout=2
        ) as sender,
        DocumentReceiver.attach(unique_name, mode="lossy"),
        DocumentReceiver.attach(unique_name) as receiver,
    ):
        collector = Collector(receiver)
        began = time.monotonic()
        send_all(sender, documents)
        elapsed = time.monotonic() - began

        assert_documents_equal(documents, collector.join())
        assert elapsed < 1


def test_lossy_receiver_keeps_a_descriptor_behind_an_event(
    unique_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliver a descriptor of many parts to a lossy receiver that sees a later event first."""
    # Without a wait for gaps, only the rule that control documents are never
    # skipped keeps the descriptor.
    monkeypatch.setattr("sharedbox.bluebox._channel.GAP_WAIT", 0)
    bundle = event_model.compose_run()
    primary = bundle.compose_descriptor(name="primary", data_keys={"x": NUMBER})
    big = bundle.compose_descriptor(
        name="big",
        data_keys={"y": {**NUMBER, "source": "y" * (2 * 1024 * 1024)}},
    )
    documents: list[Document] = [
        ("start", dict(bundle.start_doc)),
        ("descriptor", dict(primary.descriptor_doc)),
        ("event", dict(primary.compose_event(data={"x": 1.0}, timestamps={"x": 1.0}))),
        ("descriptor", dict(big.descriptor_doc)),
        ("event", dict(primary.compose_event(data={"x": 2.0}, timestamps={"x": 2.0}))),
        ("stop", dict(bundle.compose_stop())),
    ]
    with (
        DocumentSender.create(unique_name) as sender,
        DocumentReceiver.attach(unique_name, mode="lossy") as receiver,
    ):
        collector = Collector(receiver)
        send_all(sender, documents)

        assert_documents_equal(documents, collector.join())


def test_closed_sender_ends_receivers(unique_name: str) -> None:
    """Deliver what the receiver holds when the sender closes, then raise EndOfStream within 1 s."""
    documents = run_documents()[:6]
    with (
        DocumentSender.create(unique_name) as sender,
        DocumentReceiver.attach(unique_name) as receiver,
    ):
        collector = Collector(receiver, pause_after=0)
        send_all(sender, documents)
        collector.resume.set()
        time.sleep(0.2)
        closed_at = time.monotonic()
        sender.close()

        assert_documents_equal(documents, collector.join())
        assert collector.ended_at is not None
        assert collector.ended_at - closed_at < 1


def test_event_streams_are_listed_while_they_exist(
    unique_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """List each event stream in the channel's state from before it is created until it is removed."""
    seen: list[tuple[str, list[str]]] = []
    create = SharedStream.create
    unlink = SharedStream.unlink

    def listed() -> list[str]:
        with ChannelState.attach(f"{unique_name}:state") as box:
            names: list[str] = cbor2.loads(box.event_streams)
            return names

    def recording_create(item_type: Any, name: str, **options: Any) -> Any:
        if name.startswith(f"{unique_name}:e"):
            seen.append((name, listed()))
        return create(item_type, name, **options)

    def recording_unlink(name: str) -> None:
        if name.startswith(f"{unique_name}:e"):
            seen.append((name, listed()))
        unlink(name)

    # A sender killed between creating or removing a stream and updating the
    # list would leave a stream the next sender of the channel cannot remove.
    monkeypatch.setattr(SharedStream, "create", recording_create)
    monkeypatch.setattr(SharedStream, "unlink", recording_unlink)
    with DocumentSender.create(unique_name) as sender:
        send_all(sender, run_documents())

        assert listed() == []
    assert len(seen) == 4
    assert all(name in names for name, names in seen)


def test_stale_name_is_taken_again(unique_name: str) -> None:
    """Create a channel whose previous creator exited without closing it."""
    process = CONTEXT.Process(target=start_run_and_exit, args=(unique_name,))
    process.start()
    process.join(30)
    assert process.exitcode == 0

    with (
        DocumentSender.create(unique_name) as sender,
        DocumentReceiver.attach(unique_name) as receiver,
    ):
        sender("future_kind", {"x": 1})
        assert receiver.receive(timeout=5) == ("future_kind", {"x": 1})


def test_running_name_is_refused(unique_name: str) -> None:
    """Refuse to create a channel whose sender still lives."""
    with DocumentSender.create(unique_name), pytest.raises(SegmentExistsError):
        DocumentSender.create(unique_name)
