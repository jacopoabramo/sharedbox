"""Channels of bluesky documents: a control stream and one event stream per descriptor."""

import collections
import concurrent.futures
import contextlib
import dataclasses
import time
from collections.abc import Iterator, Mapping
from concurrent.futures import Future
from typing import Annotated, Any, Final, Literal, NoReturn, Self

import cbor2

from .._box import SharedBox
from .._native import (
    EndOfStream,
    KindMismatchError,
    ReaderReleased,
    SchemaMismatchError,
    SegmentNotFoundError,
    StreamClosedError,
)
from .._status import SegmentStatus, segment_status
from .._stream import SharedReceiveStream, SharedSendStream, SharedStream
from .._types import Capacity
from ._codec import decode_document, encode_document, split_parts
from ._layout import EventLayout

CONTROL_ITEM_SIZE: Final = 16 * 1024
# The largest bytes field a box can hold.
STATE_SIZE: Final = 1024 * 1024
# How long a lossy or latest receiver waits for an item missing from the order.
GAP_WAIT: Final = 0.01


@dataclasses.dataclass(frozen=True)
class ControlItem:
    """One part of a document on a channel's control stream."""

    seq: int
    part: int
    parts: int
    data: Annotated[bytes, Capacity(CONTROL_ITEM_SIZE)]


class ChannelState(SharedBox):
    """The settings of a channel and the run under way on it, for receivers that attach mid-run.

    `seq` is the number of the document that wrote the state, and
    `event_streams` and `state` are CBOR: the names of the run's event
    streams, and the run's start, descriptors and stream names. A state
    larger than a field holds is left out, and `too_large` set.
    """

    seq: int
    string_capacity: int
    page_rows: int
    too_large: bool
    event_streams: Annotated[bytes, Capacity(STATE_SIZE)]
    state: Annotated[bytes, Capacity(STATE_SIZE)]


class ReceiverReleasedError(RuntimeError):
    """The sender released the receiver, which lost documents and closed.

    Attach a new receiver to go on from the run's state.
    """


class LateJoinError(RuntimeError):
    """The run under way has a state too large for the channel to store.

    A receiver can attach again once the run has stopped.
    """


@dataclasses.dataclass(eq=False)
class EventStream:
    """The stream that carries the events of one descriptor, on the sender's side."""

    stream: SharedStream[Any, Any]
    sender: SharedSendStream[Any, Any]
    layout: EventLayout
    sent: int = 0
    readers_checked: bool = False


@dataclasses.dataclass(eq=False)
class Source:
    """A reader of one stream of a channel, and what the receiver holds from it.

    `taken` is the position of the last item the receiver took from the
    reader, and `head` the next whole document, with its sequence number.
    With `from_start`, the reader's first item must be the stream's first.
    """

    stream: SharedStream[Any, Any]
    reader: SharedReceiveStream[Any, Any]
    layout: EventLayout | None
    future: Future[Any] | None = None
    parts: list[Any] = dataclasses.field(default_factory=list)
    head: tuple[int, str, dict[str, Any]] | None = None
    taken: int | None = None
    ended: bool = False
    from_start: bool = False


def remove_channel(name: str) -> None:
    """Remove the names of channel `name`: its state, its control stream and the event streams the state lists."""
    try:
        old = ChannelState.attach(f"{name}:state")
    except (SegmentNotFoundError, SchemaMismatchError, KindMismatchError):
        names: list[str] = []
    else:
        try:
            names = cbor2.loads(old.event_streams)
        finally:
            old.close()
    for each in [*names, f"{name}:control"]:
        with contextlib.suppress(SegmentNotFoundError):
            SharedStream.unlink(each)
    with contextlib.suppress(SegmentNotFoundError):
        ChannelState.unlink(f"{name}:state")


class DocumentSender:
    """Sends bluesky documents on a channel that receivers in any process attach to.

    Call it with each document's name and the document, as a `RunEngine`
    callback is called. Events and event pages go to an event stream per
    descriptor, every other document to the channel's control stream.
    Closing the sender ends the channel: receivers deliver what they hold,
    then raise `EndOfStream`.
    """

    def __init__(
        self,
        name: str,
        control: SharedStream[ControlItem, Any],
        box: ChannelState,
        *,
        event_capacity: int,
        max_readers: int,
        string_capacity: int,
        page_rows: int,
        control_timeout: float,
        event_timeout: float | None,
        on_full: Literal["wait", "release"],
    ) -> None:
        self.name = name
        self._control = control
        self._box = box
        self._control_sender = control.sender(on_full="release")
        self._event_capacity = event_capacity
        self._max_readers = max_readers
        self._string_capacity = string_capacity
        self._page_rows = page_rows
        self._control_timeout = control_timeout
        self._event_timeout = event_timeout
        self._on_full: Literal["wait", "release"] = on_full
        self._events: dict[str, EventStream] = {}
        self._state: dict[str, Any] = {}
        self._seq = 0
        self._closed = False

    @classmethod
    def create(
        cls,
        name: str,
        *,
        event_capacity: int = 8,
        max_readers: int = 8,
        control_capacity: int = 64,
        string_capacity: int = 256,
        page_rows: int = 16,
        control_timeout: float = 5.0,
        event_timeout: float | None = None,
        on_full: Literal["wait", "release"] = "wait",
    ) -> Self:
        """Create the channel `name`.

        A channel whose creator has exited is removed first. The run's start
        and descriptors are kept for receivers that attach mid-run while
        their CBOR takes at most 1 MiB; a larger run refuses such receivers.

        Parameters
        ----------
        event_capacity
            Items each event stream holds.
        max_readers
            Receivers that may be attached at once.
        control_capacity
            Parts of 16 KiB the control stream holds.
        string_capacity
            Most bytes of UTF-8 in a string of an event.
        page_rows
            Most rows of an event page one item of an event stream holds.
        control_timeout
            Seconds a control document waits for a receiver that stopped
            reading before the sender releases it.
        event_timeout
            Seconds an event waits for a lossless receiver a full ring
            behind; None waits as long as it takes.
        on_full
            What an event send does when `event_timeout` passes: `"wait"`
            raises `TimeoutError`, `"release"` releases the receiver.

        Raises
        ------
        SegmentExistsError
            If the creator of a channel of that name still runs.
        """
        if any(
            segment_status(f"{name}:{part}") is SegmentStatus.CREATOR_EXITED
            for part in ("state", "control")
        ):
            remove_channel(name)
        box = ChannelState.create(
            f"{name}:state",
            seq=0,
            string_capacity=string_capacity,
            page_rows=page_rows,
            too_large=False,
            event_streams=cbor2.dumps([]),
            state=encode_document("state", {}),
        )
        try:
            control = SharedStream.create(
                ControlItem,
                f"{name}:control",
                capacity=control_capacity,
                max_readers=max_readers,
            )
        except BaseException:
            box.close()
            ChannelState.unlink(f"{name}:state")
            raise
        try:
            return cls(
                name,
                control,
                box,
                event_capacity=event_capacity,
                max_readers=max_readers,
                string_capacity=string_capacity,
                page_rows=page_rows,
                control_timeout=control_timeout,
                event_timeout=event_timeout,
                on_full=on_full,
            )
        except BaseException:
            control.close()
            box.close()
            raise

    def __call__(self, name: str, doc: Mapping[str, Any]) -> None:
        """Send the document `doc` named `name`.

        Raises
        ------
        ValueError
            If a descriptor holds a key an event stream cannot carry, an
            event does not match its descriptor, or its descriptor was not
            sent on this channel. Nothing is sent then.
        TypeError
            If a document holds a value that cannot be encoded.
        TimeoutError
            If an event waited `event_timeout` for a lossless receiver and
            `on_full` is `"wait"`.
        """
        if name in ("event", "event_page"):
            self._send_event(name, doc)
            return
        data = encode_document(name, doc)
        parts = split_parts(data, CONTROL_ITEM_SIZE)
        state: dict[str, Any] | None = None
        layout = None
        match name:
            case "start":
                state = {"start": doc, "descriptors": [], "event_streams": []}
            case "descriptor":
                # Receivers build the layout from the decoded descriptor, whose
                # keys canonical CBOR has put in its own order.
                layout = EventLayout.from_descriptor(
                    decode_document(data)[1],
                    string_capacity=self._string_capacity,
                    page_rows=self._page_rows,
                )
            case "stop":
                state = {}
                # Before the stop, since a receiver closes its readers once it
                # delivers it.
                for events in self._events.values():
                    if not events.readers_checked:
                        self._wait_for_readers(events)
        self._seq += 1
        if layout is not None:
            stream_name = f"{self.name}:e{self._seq}"
            stream = SharedStream.create(
                layout.item_type,
                stream_name,
                capacity=self._event_capacity,
                max_readers=self._max_readers,
            )
            self._events[doc["uid"]] = EventStream(
                stream, stream.sender(self._on_full), layout
            )
            state = {
                "start": self._state.get("start"),
                "descriptors": [*self._state.get("descriptors", ()), doc],
                "event_streams": [*self._state.get("event_streams", ()), stream_name],
            }
        items = [
            ControlItem(self._seq, index, len(parts), part)
            for index, part in enumerate(parts)
        ]
        if state is not None:
            self._write_state(state)
        for item in items:
            self._control_sender.send(item, self._control_timeout)
        if name == "stop":
            self._close_events()

    def _send_event(self, name: str, doc: Mapping[str, Any]) -> None:
        events = self._events.get(doc.get("descriptor", ""))
        if events is None:
            raise ValueError(
                f"{name} document: descriptor {doc.get('descriptor')!r} was not "
                "sent on this channel"
            )
        items = events.layout.pack(name, doc)
        self._seq += 1
        if (
            not events.readers_checked
            and events.sent + len(items) > self._event_capacity
        ):
            self._wait_for_readers(events)
        for item in items:
            events.sender.send(
                dataclasses.replace(item, seq=self._seq), self._event_timeout
            )
        events.sent += len(items)

    def _wait_for_readers(self, events: EventStream) -> None:
        """Wait until each lossless receiver has opened the event stream, at most `control_timeout`.

        A receiver opens it only once it delivers the descriptor, so a
        lossless one would otherwise lose the events sent before that when
        the ring is overwritten or the stream closed at the stop.
        """
        deadline = time.monotonic() + self._control_timeout
        while self._lossless_readers_missing(events) and time.monotonic() < deadline:
            time.sleep(0.001)
        events.readers_checked = True

    def _lossless_readers_missing(self, events: EventStream) -> bool:
        """Return whether a lossless receiver has not opened the event stream yet.

        Every receiver reads the control stream with a lossless reader, and a
        lossy or latest one also holds a lossy reader there that it never
        reads, so per process the lossless receivers are the lossless control
        readers less the lossy ones. A lossless control reader more than a
        full ring behind was released and is not waited for.
        """
        missing: collections.Counter[int] = collections.Counter()
        capacity = self._control.capacity
        for reader in self._control.statistics().readers:
            if reader.mode != "lossless":
                missing[reader.pid] -= 1
            # Only a released reader falls more than a full ring behind.
            elif reader.lag <= capacity:
                missing[reader.pid] += 1
        for reader in events.stream.statistics().readers:
            if reader.mode == "lossless":
                missing[reader.pid] -= 1
        return any(count > 0 for count in missing.values())

    def _write_state(self, state: dict[str, Any]) -> None:
        """Write the run's state, before the document that changes it is sent."""
        data = encode_document("state", state)
        too_large = len(data) > STATE_SIZE
        self._box.update(
            seq=self._seq,
            too_large=too_large,
            event_streams=cbor2.dumps(list(state.get("event_streams", ()))),
            state=b"" if too_large else data,
        )
        self._state = state

    def _close_events(self) -> None:
        for events in self._events.values():
            events.stream.close()
            with contextlib.suppress(SegmentNotFoundError):
                SharedStream.unlink(events.stream.name)
        self._events.clear()

    def close(self) -> None:
        """End the channel and remove its names; calling it again does nothing."""
        if self._closed:
            return
        self._closed = True
        self._close_events()
        self._control.close()
        self._box.close()
        for unlink, each in (
            (SharedStream.unlink, self._control.name),
            (ChannelState.unlink, f"{self.name}:state"),
        ):
            with contextlib.suppress(SegmentNotFoundError):
                unlink(each)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class DocumentReceiver:
    """Receives the documents of a channel, in the order they were sent.

    The control stream is read without loss whatever the mode; the mode
    applies to the event streams. A receiver attached mid-run delivers the
    run's start and descriptors first, then the documents from its attach
    point on.
    """

    def __init__(
        self,
        name: str,
        mode: Literal["lossless", "lossy", "latest"],
        control: SharedStream[ControlItem, Any],
    ) -> None:
        self.name = name
        self.mode: Literal["lossless", "lossy", "latest"] = mode
        self._sources = [Source(control, control.reader("lossless", "newest"), None)]
        # Never read: it tells the sender not to wait for this receiver's event
        # readers. Closing the control stream closes it.
        self._marker = None if mode == "lossless" else control.reader("lossy", "newest")
        self._prelude: collections.deque[tuple[str, dict[str, Any]]] = (
            collections.deque()
        )
        self._closed = False
        self._string_capacity = 0
        self._page_rows = 0
        self._last = 0

    def _join(self, box: Mapping[str, Any]) -> None:
        """Take the settings from the channel's state box, queue the run's start and descriptors, and open their event streams.

        Control items up to the box's `seq` are dropped, because the state
        already holds what they carry.
        """
        self._string_capacity = box["string_capacity"]
        self._page_rows = box["page_rows"]
        self._last = box["seq"]
        if box["too_large"]:
            raise LateJoinError(
                f"channel {self.name!r}: the run under way has a state larger than "
                "the channel stores; attach again after its stop"
            )
        state: dict[str, Any] = decode_document(box["state"])[1]
        if state.get("start") is None:
            return
        self._prelude.append(("start", state["start"]))
        for descriptor, stream_name in zip(
            state["descriptors"], state["event_streams"], strict=True
        ):
            self._prelude.append(("descriptor", descriptor))
            self._open(descriptor, stream_name, "newest")

    @classmethod
    def attach(
        cls, name: str, *, mode: Literal["lossless", "lossy", "latest"] = "lossless"
    ) -> Self:
        """Attach to the channel `name`.

        Raises
        ------
        SegmentNotFoundError
            If there is no channel of that name.
        LateJoinError
            If the run under way has a state too large for the channel.
        """
        if mode not in ("lossless", "lossy", "latest"):
            raise ValueError(f"mode is 'lossless', 'lossy' or 'latest'; got {mode!r}")
        control = SharedStream.attach(ControlItem, f"{name}:control")
        try:
            receiver = cls(name, mode, control)
        except BaseException:
            control.close()
            raise
        try:
            # The control reader opened first, so the state covers every item
            # before the reader's start.
            with ChannelState.attach(f"{name}:state") as box:
                values = box.snapshot()
            receiver._join(values)
        except BaseException:
            receiver.close()
            raise
        return receiver

    def _open(
        self,
        descriptor: Mapping[str, Any],
        stream_name: str,
        start: Literal["newest", "oldest"],
    ) -> None:
        layout = EventLayout.from_descriptor(
            descriptor, string_capacity=self._string_capacity, page_rows=self._page_rows
        )
        # A lossless receiver that opens a stream at its descriptor must get
        # every item; the sender stops waiting for it after control_timeout.
        lossless = start == "oldest" and self.mode == "lossless"
        try:
            stream = SharedStream.attach(layout.item_type, stream_name)
        except SegmentNotFoundError:
            if lossless:
                self._release()
            # The run stopped and its streams were removed; the stop follows.
            return
        self._sources.append(
            Source(stream, stream.reader(self.mode, start), layout, from_start=lossless)
        )

    def receive(self, timeout: float | None = None) -> tuple[str, dict[str, Any]]:
        """Return the next document's name and the document.

        Raises
        ------
        EndOfStream
            If the sender closed or died and every document it sent that
            this receiver holds was delivered.
        ReceiverReleasedError
            If the sender released this receiver, or a lossless receiver lost
            events of a stream it opened too late; it is closed then.
        TimeoutError
            If `timeout` seconds pass first.
        StreamClosedError
            If the receiver is closed.
        """
        if self._closed:
            raise StreamClosedError(f"channel {self.name!r}: the receiver is closed")
        if self._prelude:
            return self._prelude.popleft()
        deadline = None if timeout is None else time.monotonic() + timeout
        gap_since: float | None = None
        while True:
            self._take_done()
            heads = [source for source in self._sources if source.head is not None]
            waiting = [
                source
                for source in self._sources
                if source.head is None and not source.ended
            ]
            if not heads and not waiting:
                raise EndOfStream(f"channel {self.name!r} has ended")
            wait = None if deadline is None else max(0.0, deadline - time.monotonic())
            if heads:
                low = min(heads, key=lambda source: source.head[0])  # type: ignore[index]
                assert low.head is not None
                if low.head[0] == self._last + 1:
                    return self._deliver(low)
                pending = [source for source in waiting if self._pending(source)]
                if not pending:
                    return self._deliver(low)
                # A control document is never skipped: its parts are already in
                # the ring, so waiting for them holds back no sender.
                if self.mode != "lossless" and all(
                    source.layout is not None for source in pending
                ):
                    now = time.monotonic()
                    gap_since = now if gap_since is None else gap_since
                    left = GAP_WAIT - (now - gap_since)
                    if left <= 0:
                        return self._deliver(low)
                    wait = left if wait is None else min(wait, left)
            for source in waiting:
                if source.future is None:
                    source.future = source.reader.receive_future()
            done, _ = concurrent.futures.wait(
                [source.future for source in waiting if source.future is not None],
                timeout=wait,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if not done and deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(
                    f"channel {self.name!r}: no document within {timeout} s"
                )

    def _pending(self, source: Source) -> bool:
        """Return whether the reader of `source` has an item the receiver has not taken."""
        if source.reader.position != source.taken:
            return True
        sent = source.stream.statistics().sent
        return sent > (0 if source.taken is None else source.taken + 1)

    def _take_done(self) -> None:
        for source in self._sources:
            if source.future is None or not source.future.done():
                continue
            future, source.future = source.future, None
            try:
                item = future.result()
            except EndOfStream:
                source.ended = True
                continue
            except ReaderReleased:
                self._release()
            source.taken = source.reader.position
            if source.from_start and source.taken != 0:
                self._release()
            source.from_start = False
            self._add(source, item)

    def _release(self) -> NoReturn:
        """Close the receiver, which lost documents, and raise `ReceiverReleasedError`."""
        self.close()
        raise ReceiverReleasedError(
            f"channel {self.name!r}: the sender released this receiver, which lost "
            "documents; attach again"
        )

    def _add(self, source: Source, item: Any) -> None:
        """Add an item to the parts `source` holds, and make its head once a document is whole."""
        if item.seq <= self._last:
            return
        if item.part == 0:
            source.parts = [item]
        elif (
            source.parts
            and source.parts[-1].seq == item.seq
            and item.part == len(source.parts)
        ):
            source.parts.append(item)
        else:
            source.parts = []
            return
        if len(source.parts) < item.parts:
            return
        parts, source.parts = source.parts, []
        if source.layout is None:
            name, doc = decode_document(b"".join(part.data for part in parts))
        else:
            name, doc = source.layout.unpack(parts)
        source.head = (item.seq, name, doc)

    def _deliver(self, source: Source) -> tuple[str, dict[str, Any]]:
        assert source.head is not None
        seq, name, doc = source.head
        source.head = None
        self._last = seq
        if name == "descriptor":
            self._open(doc, f"{self.name}:e{seq}", "oldest")
        elif name == "stop":
            for each in self._sources[1:]:
                each.stream.close()
            del self._sources[1:]
        return name, doc

    def __iter__(self) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield each document's name and the document until the channel ends."""
        while True:
            try:
                yield self.receive()
            except EndOfStream:
                return

    def close(self) -> None:
        """Close the receiver's readers; calling it again does nothing."""
        self._closed = True
        for source in self._sources:
            source.stream.close()
        self._sources.clear()
        self._prelude.clear()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
