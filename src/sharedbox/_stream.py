"""SharedStream: a ring of typed items in shared memory, written by one sender and read by several readers."""

from __future__ import annotations

import asyncio
import atexit
import collections
import contextlib
import functools
import hashlib
import itertools
import logging
import os
import queue
import sys
import threading
import time
import weakref
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from typing import Any, Final, Generic, Literal, TypeVar, cast, overload

from psygnal import Signal, SignalGroup, SignalInstance

from ._box import check_name
from ._native import (
    EndOfStream,
    Reader,
    Segment,
    Sender,
    Stream,
    StreamClosedError,
    Types,
    WouldBlock,
    _Interrupted,
)
from ._types import Table, TypeSpec, parse

T = TypeVar("T")
Mode = Literal["lossless", "lossy", "latest"]
Start = Literal["newest", "oldest"]
MODES: Final[dict[str, int]] = {"lossless": 1, "lossy": 2, "latest": 3}
MODE_NAMES: Final[dict[int, Mode]] = {1: "lossless", 2: "lossy", 3: "latest"}
STEP: Final = 1.0
EXIT_WAIT: Final = 2.0
logger = logging.getLogger("sharedbox")
WORKER_THREADS: set[threading.Thread] = set()
READER_IDS = itertools.count(1)
YIELD_EVERY: Final = 64
YIELD_AFTER: Final = 0.001
LIVE_ENDS: weakref.WeakSet[StreamSender[Any] | StreamReader[Any]] = weakref.WeakSet()


@dataclass(frozen=True)
class Item:
    """How a stream stores its items, built once from the item annotation."""

    label: str
    kind: str
    types: Types
    entry: int
    """The item's `capacity_and_kind` word, as a box field table entry has."""
    schema_hash: int
    spec: TypeSpec
    """The parsed item type, which `receive_into` checks `out` against."""
    has_arrays: bool
    """Whether an array is reachable from the item through records and tuples."""


def item_of(hint: object) -> Item:
    """Return how a stream stores items annotated `hint`.

    Raises
    ------
    TypeError
        If a box field could not hold `hint`, or it names a box class.
    """
    label = getattr(hint, "__qualname__", None) or repr(hint)
    spec = parse(hint, label)
    table = Table()
    low = table.describe(spec) if spec.described else spec.entry_low
    if spec.bytearray:
        table.bytearrays.append((-1, 0))
    types = Types(
        [(0, low, spec.code)], [label], bytes(table.data), table.info, table.bytearrays
    )
    digest = hashlib.sha256(f"stream|{spec.text}".encode()).digest()
    return Item(
        label,
        spec.kind,
        types,
        low | spec.code << 24,
        int.from_bytes(digest[:8], "little"),
        spec,
        holds_array(spec),
    )


def holds_array(spec: TypeSpec) -> bool:
    """Return whether an array is reachable from `spec` through records and tuples."""
    match spec.kind:
        case "array":
            return True
        case "record" | "tuple":
            return any(holds_array(m.type) for m in spec.members)
        case _:
            return False


def targets_of(
    spec: TypeSpec, out: object, label: str, path: tuple[int, ...] = ()
) -> list[tuple[tuple[int, ...], object]]:
    """Return `(member path, array)` for each array in `out`, which must have the shape of `spec`.

    Raises
    ------
    TypeError
        If `out` or an entry inside it does not match the member it stands
        for: a tuple of another length, a mapping with a name that is not a
        member, or an array for a member that is not an array, such as a
        collection.
    """
    if out is None:
        return []
    match spec.kind:
        case "array":
            return [(path, out)]
        case "tuple":
            if not isinstance(out, tuple) or len(out) != len(spec.members):
                raise TypeError(
                    f"{label}: out is a tuple of {len(spec.members)} entries, "
                    "each an array, None or an out of the member's shape"
                )
            return [
                target
                for i, (m, entry) in enumerate(zip(spec.members, out))
                for target in targets_of(m.type, entry, f"{label}[{i}]", (*path, i))
            ]
        case "record":
            if not isinstance(out, Mapping):
                raise TypeError(
                    f"{label}: out maps member names to arrays, None or an out of the member's shape"
                )
            positions = {m.name: i for i, m in enumerate(spec.members)}
            for name in out:
                if name not in positions:
                    raise TypeError(
                        f"{label} has no member {name!r}; its members: {', '.join(positions)}"
                    )
            return [
                target
                for name, entry in out.items()
                for target in targets_of(
                    spec.members[positions[name]].type,
                    entry,
                    f"{label}.{name}",
                    (*path, positions[name]),
                )
            ]
        case "list" | "set" | "dict":
            raise TypeError(
                f"{label} is a {spec.kind}, which cannot hold an array; "
                "give None for it or leave it out"
            )
        case "optional" | "union":
            raise TypeError(
                f"{label} is {'an' if spec.kind == 'optional' else 'a'} {spec.kind} member, "
                "which cannot be read into an array; "
                "give None for it or leave it out"
            )
        case _:
            raise TypeError(
                f"{label} is not an array member, so no array can be given for it; "
                "give None for it or leave it out"
            )


@dataclass(frozen=True)
class ReaderStatistics:
    """One open reader of a stream, as [`SharedStream.statistics`][sharedbox.SharedStream.statistics] found it."""

    mode: Mode
    position: int
    """The position of the item the reader receives next.

    Right after a receive it is one more than
    [`StreamReader.position`][sharedbox.StreamReader.position], which is the
    position of the item received.
    """
    lag: int
    """Items sent that the reader has not received or skipped yet."""
    pid: int


@dataclass(frozen=True)
class StreamStatistics:
    """A stream's state at one moment: what was sent, what the ring holds, and who reads it."""

    capacity: int
    sent: int
    buffered: int
    """Items the ring holds: the last `capacity` sent, or fewer."""
    ended: bool
    sender_pid: int
    """The process holding the sender, 0 when none does."""
    readers: tuple[ReaderStatistics, ...]


class SharedStream(Generic[T]):
    """A ring of items in shared memory, sent by one process and received by up to `max_readers` readers.

    Create it with [`create`][sharedbox.SharedStream.create] in one process and
    open it with [`attach`][sharedbox.SharedStream.attach] in others. Every
    item has the type given there: any type a `SharedBox` field takes, except
    a reference to a box.
    """

    __slots__ = (
        "__weakref__",
        "_ends",
        "_hint",
        "_item",
        "_name",
        "_native",
        "_sender",
    )

    _hint: object
    _item: Item
    _name: str
    _native: Stream
    _ends: weakref.WeakSet[StreamSender[T] | StreamReader[T]]
    _sender: StreamSender[T] | None

    @overload
    @classmethod
    def create(
        cls, item_type: type[T], name: str, *, capacity: int, max_readers: int = 8
    ) -> SharedStream[T]: ...
    @overload
    @classmethod
    def create(
        cls, item_type: object, name: str, *, capacity: int, max_readers: int = 8
    ) -> SharedStream[Any]: ...
    @classmethod
    def create(
        cls, item_type: object, name: str, *, capacity: int, max_readers: int = 8
    ) -> SharedStream[Any]:
        """Create the stream `name` holding `capacity` items of `item_type`.

        Parameters
        ----------
        capacity
            Items the ring holds, at least 2. A lossless reader may fall
            this many items behind before `send` waits for it.
        max_readers
            Readers that may be open at once, 1 to 4095.

        Raises
        ------
        SegmentExistsError
            If the name is taken.
        TypeError
            If a box field could not hold `item_type`.
        ValueError
            If the name breaks the naming rules, or `capacity` or
            `max_readers` is out of range.
        """
        item = item_of(item_type)
        native = Stream.create(
            check_name(name),
            item.types,
            item.entry,
            item.schema_hash,
            capacity,
            max_readers,
        )
        return cls._wrap(item_type, item, native)

    @overload
    @classmethod
    def attach(cls, item_type: type[T], name: str) -> SharedStream[T]: ...
    @overload
    @classmethod
    def attach(cls, item_type: object, name: str) -> SharedStream[Any]: ...
    @classmethod
    def attach(cls, item_type: object, name: str) -> SharedStream[Any]:
        """Open the stream `name`, whose items must have the type `item_type`.

        Raises
        ------
        SegmentNotFoundError
            If no stream has that name, or it does not become one within 1 s.
        KindMismatchError
            If the name holds a segment of another kind, such as a box.
        SchemaMismatchError
            If the stream holds items of another type.
        """
        item = item_of(item_type)
        native = Stream.attach(
            check_name(name), item.types, item.entry, item.schema_hash
        )
        return cls._wrap(item_type, item, native)

    @staticmethod
    def unlink(name: str) -> None:
        """Remove the name `name` on Linux, as `SharedBox.unlink` does; a no-op on Windows."""
        Segment.unlink(check_name(name))

    @classmethod
    def _wrap(cls, hint: object, item: Item, native: Stream) -> SharedStream[Any]:
        stream: SharedStream[Any] = object.__new__(cls)
        stream._hint = hint
        stream._item = item
        stream._native = native
        stream._name = native.name
        stream._ends = weakref.WeakSet()
        stream._sender = None
        return stream

    @property
    def name(self) -> str:
        return self._name

    @property
    def capacity(self) -> int:
        return self._native.capacity

    @property
    def max_readers(self) -> int:
        return self._native.max_readers

    @property
    def closed(self) -> bool:
        return self._native.closed

    def sender(self) -> StreamSender[T]:
        """Return the stream's sender for this process.

        Raises
        ------
        StreamBusyError
            If another live end holds the sender.
        EndOfStream
            If the stream has ended: its sender closed, or a reader found the
            sender's process dead. There is no second sender after that.

        Notes
        -----
        A sender whose process died is replaced, unless a reader already
        found it dead, which ended the stream.

        The stream keeps its sender until it is closed, so dropping the
        returned object does not end the stream, and another `sender()` call
        in this process raises `StreamBusyError` until then.
        """
        end: StreamSender[T] = StreamSender(self, self._native.sender())
        self._ends.add(end)
        self._sender = end
        return end

    def reader(
        self, mode: Mode = "lossless", start: Start = "newest"
    ) -> StreamReader[T]:
        """Open a reader.

        Parameters
        ----------
        mode
            `"lossless"` receives every item and holds the sender back when
            a full ring behind; `"lossy"` skips the items the sender
            overwrote; `"latest"` always receives the newest item.
        start
            `"newest"` begins at the newest item sent, `"oldest"` at the
            oldest the ring still holds.

        Raises
        ------
        ValueError
            If `mode` or `start` is none of the values above.
        WaiterSlotsFullError
            If `max_readers` readers are open.
        """
        if mode not in MODES:
            raise ValueError(f"mode is 'lossless', 'lossy' or 'latest'; got {mode!r}")
        if start not in ("newest", "oldest"):
            raise ValueError(f"start is 'newest' or 'oldest'; got {start!r}")
        end: StreamReader[T] = StreamReader(
            self, self._native.reader(MODES[mode], start == "newest")
        )
        self._ends.add(end)
        return end

    def statistics(self) -> StreamStatistics:
        """Return what was sent, what the ring holds, and each open reader's state."""
        sent, ended, sender_pid, readers = self._native.statistics()
        return StreamStatistics(
            self.capacity,
            sent,
            min(sent, self.capacity),
            ended,
            sender_pid,
            tuple(
                ReaderStatistics(
                    MODE_NAMES[mode],
                    min(position, sent),
                    max(sent - position, 0),
                    pid,
                )
                for position, mode, pid in readers
            ),
        )

    def close(self) -> None:
        """Close every sender and reader made from this object, then let go of the stream.

        Closing the sender ends the stream for its readers.

        Other processes keep the stream. Using a closed stream raises
        [`StreamClosedError`][sharedbox.StreamClosedError].
        """
        for end in list(self._ends):
            end.close()
        self._native.close()

    def __enter__(self) -> SharedStream[T]:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __reduce__(self) -> tuple[Callable[..., SharedStream[Any]], tuple[object, str]]:
        return (attach_stream, (self._hint, self.name))

    def __repr__(self) -> str:
        state = " closed" if self.closed else ""
        return f"<SharedStream[{self._item.label}] {self.name!r}{state}>"


def attach_stream(item_type: object, name: str) -> SharedStream[Any]:
    """Attach to the stream a pickle refers to."""
    return SharedStream.attach(item_type, name)


def open_reader(item_type: object, name: str, mode: Mode) -> StreamReader[Any]:
    """Open a new reader of the stream a pickled reader refers to."""
    return SharedStream.attach(item_type, name).reader(mode)


class CallFuture(Future[Any]):
    """The future of one call on a worker; `position` is where a receive stopped."""

    position: int | None = None


Call = Callable[[CallFuture], Any]


class Worker:
    """A daemon thread that runs one stream end's waiting calls, one at a time, in the order submitted."""

    def __init__(self, stream: str, role: str, after: Callable[[], None]) -> None:
        self._stream = stream
        self._after = after
        self._calls: queue.SimpleQueue[tuple[CallFuture, Call] | None] = (
            queue.SimpleQueue()
        )
        self._stopped = False
        self.thread = threading.Thread(
            target=self._run, name=f"sharedbox:{stream}:{role}", daemon=True
        )
        self.thread.start()
        WORKER_THREADS.add(self.thread)

    def submit(self, call: Call) -> CallFuture:
        future = CallFuture()
        self._calls.put((future, call))
        return future

    def stop(self) -> None:
        self._stopped = True
        self._calls.put(None)

    def _run(self) -> None:
        try:
            self._serve()
        finally:
            WORKER_THREADS.discard(self.thread)

    def _run_one(self, future: CallFuture, call: Call) -> None:
        """Run one call in its own frame, so nothing it refers to outlives it while the thread waits.

        A call still queued when its end closed raises `StreamClosedError`,
        as a running one does; only its awaiter cancels it.
        """
        if not future.set_running_or_notify_cancel():
            return
        if self._stopped:
            future.set_exception(
                StreamClosedError(f"stream {self._stream!r}: this end is closed")
            )
            return
        try:
            result = call(future)
        except BaseException as error:
            future.set_exception(error)
        else:
            future.set_result(result)

    def _serve(self) -> None:
        while (entry := self._calls.get()) is not None:
            self._run_one(*entry)
            del entry
            self._after()


def finished(end: weakref.ref[End]) -> None:
    """Count a worker call of `end` as done, if the end still exists, and wake the receives waiting for its last call."""
    if (target := end()) is not None:
        with target._lock:
            target._inflight -= 1
            if target._inflight == 0:
                target._idle.notify_all()


class End:
    """What a sender and a reader share: the native end, its stream, and closing it once."""

    __slots__ = (
        "__weakref__",
        "_burst",
        "_burst_start",
        "_cancelled",
        "_idle",
        "_inflight",
        "_lock",
        "_native",
        "_shut",
        "_stream",
        "_worker",
    )

    def __init__(self, stream: SharedStream[Any], native: Sender | Reader) -> None:
        self._stream = stream
        self._native = native
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._worker: Worker | None = None
        self._cancelled: Future[Any] | None = None
        self._shut = False
        self._inflight = 0
        self._burst = 0
        self._burst_start = 0.0
        LIVE_ENDS.add(cast(Any, self))

    @property
    def closed(self) -> bool:
        return self._shut or self._native.closed

    def _submit(self, call: Call) -> CallFuture:
        with self._lock:
            return self._submit_locked(call)

    def _submit_locked(self, call: Call) -> CallFuture:
        """Queue `call` on the worker; the caller holds `_lock`."""
        if self.closed:
            raise StreamClosedError(f"stream {self._stream.name!r}: this end is closed")
        if self._worker is None:
            self._worker = Worker(
                self._stream.name,
                self._thread_role(),
                functools.partial(finished, weakref.ref(self)),
            )
            weakref.finalize(self, self._worker.stop)
        self._inflight += 1
        return self._worker.submit(call)

    _thread_role: Callable[[], str]

    def _fast(self) -> bool:
        """Return whether a call may bypass the worker: none is in flight, so order and the end's mutex are free."""
        with self._lock:
            return self._inflight == 0 and not self.closed

    async def _yield(self) -> None:
        """Give the event loop a turn every `YIELD_EVERY` fast calls or `YIELD_AFTER` seconds, whichever comes first."""
        self._burst += 1
        if (
            self._burst >= YIELD_EVERY
            or time.monotonic() - self._burst_start >= YIELD_AFTER
        ):
            self._burst = 0
            await asyncio.sleep(0)
            self._burst_start = time.monotonic()

    def _cancel(self, future: Future[Any]) -> None:
        """Stop the call behind `future`: drop it if it has not started, else interrupt its wait."""
        if future.cancel() or future.done():
            return
        with self._lock:
            self._cancelled = future
        self._native.interrupt()

    async def _wait(self, future: Future[Any]) -> Any:
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            self._cancel(future)
            raise

    def close(self) -> None:
        """Close this end; a call waiting in it raises `StreamClosedError`. Calling it again does nothing."""
        with self._lock:
            self._shut = True
            worker, self._worker = self._worker, None
            self._idle.notify_all()
        if worker is not None:
            worker.stop()
        self._native.close()

    def _after_fork(self) -> None:
        self._native._after_fork()
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._worker = None
        self._cancelled = None
        self._shut = True

    async def __aexit__(self, *exc_info: object) -> None:
        self.close()


def steps(timeout: float | None) -> Iterator[float]:
    """Yield the length of each native wait for `timeout`: `STEP` without end for None, else until it passes."""
    if timeout is None:
        while True:
            yield STEP
    deadline = time.monotonic() + timeout
    yield min(STEP, max(0.0, timeout))
    while (left := deadline - time.monotonic()) > 0:
        yield min(STEP, left)


class StreamSender(End, Generic[T]):
    """The sender of a stream: one per stream, in the process that holds it.

    [`SharedStream.sender`][sharedbox.SharedStream.sender] returns it. The
    stream keeps it open until `close()`, or until the stream is closed or
    the process dies; dropping the object does not close it.
    Closing it ends the stream: readers receive what is buffered, then
    [`EndOfStream`][sharedbox.EndOfStream].
    """

    __slots__ = ()
    _native: Sender

    def _thread_role(self) -> str:
        return "sender"

    def send(self, item: T, timeout: float | None = None) -> None:
        """Send `item`, waiting while a lossless reader is a full ring behind.

        Raises
        ------
        TimeoutError
            If `timeout` seconds pass first; the item is not sent.
        StreamClosedError
            If this sender is closed, before or during the wait.

        Notes
        -----
        An interrupt such as Ctrl-C during the call may leave the item sent
        although the call raised.
        """
        self._send(item, timeout)

    async def asend(self, item: T) -> None:
        """Send `item`, as [`send`][sharedbox.StreamSender.send] does, without blocking the event loop.

        A cancelled `asend` may still have sent its item, if a reader made
        room just as it was cancelled.
        """
        await self._yield()
        if self._fast():
            try:
                self.send_nowait(item)
                return
            except WouldBlock:
                pass
        await self._wait(self._submit(lambda future: self._send(item, None, future)))

    def send_nowait(self, item: T) -> None:
        """Send `item`, or raise [`WouldBlock`][sharedbox.WouldBlock] if a lossless reader is a full ring behind."""
        try:
            self._native.send(item, 0.0)
        except _Interrupted:
            raise WouldBlock(
                f"stream {self._stream.name!r}: a lossless reader is a full ring behind"
            ) from None

    def _send(
        self, item: T, timeout: float | None, future: CallFuture | None = None
    ) -> None:
        for step in steps(timeout):
            try:
                self._native.send(item, step)
                return
            except WouldBlock:
                continue
            except _Interrupted:
                if future is not None and self._cancelled is future:
                    self._cancelled = None
                    raise CancelledError() from None
                if self.closed:
                    raise StreamClosedError(
                        f"stream {self._stream.name!r}: the sender was closed"
                    ) from None
                continue
        raise TimeoutError(
            f"stream {self._stream.name!r}: no lossless reader made room within {timeout} s"
        )

    def close(self) -> None:
        """Close the sender and end the stream. Calling it again does nothing."""
        super().close()
        if self._stream._sender is self:
            self._stream._sender = None

    def __enter__(self) -> StreamSender[T]:
        return self

    async def __aenter__(self) -> StreamSender[T]:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __reduce__(self) -> Any:
        raise TypeError(
            "a StreamSender cannot be pickled; send the SharedStream and call sender() "
            "in the other process"
        )


class CountedSignal(SignalInstance):
    """A psygnal signal that tells its group each time a callback connects or disconnects."""

    def _append_slot(self, slot: Any) -> None:
        super()._append_slot(slot)
        self._changed()

    def _remove_slot(self, slot: Any) -> None:
        super()._remove_slot(slot)
        self._changed()

    def _changed(self) -> None:
        group = self.instance
        if isinstance(group, ReaderEvents):
            group._sharedbox_changed()


class ReaderEvents(SignalGroup):
    """The psygnal signal group of a stream reader.

    It is the type of [`StreamReader.events`][sharedbox.StreamReader.events].
    """

    received = Signal(object, int, signal_instance_class=CountedSignal)
    """Emitted as `(item, position)` for each item the reader receives."""
    ended = Signal()
    """Emitted once per delivery, when it reaches the end of the stream."""

    def __init__(self, reader: StreamReader[Any] | None = None) -> None:
        """Create the signal group of a stream reader.

        Parameters
        ----------
        reader
            The reader whose delivery starts and stops with the `received`
            callbacks; None for a group tied to no reader.
        """
        self._reader = None if reader is None else weakref.ref(reader)
        super().__init__()

    def _sharedbox_changed(self) -> None:
        reader = None if self._reader is None else self._reader()
        if reader is not None:
            reader._reconcile()


class StreamReader(End, Iterator[T], AsyncIterator[T], Generic[T]):
    """A reader of a stream, in the process that opened it.

    [`SharedStream.reader`][sharedbox.SharedStream.reader] returns it.
    `for item in reader` and `async for item in reader` receive until the
    stream ends.

    Notes
    -----
    Receives made with `receive` and the like must not overlap receives made
    with `receive_future` or `async for` on the same reader: an item that an
    awaiting task gave up is kept and returned by the next receive, which may
    then come after a later item.
    """

    __slots__ = (
        "_carry",
        "_delivering",
        "_events",
        "_generation",
        "_position",
    )
    _native: Reader

    def __init__(self, stream: SharedStream[Any], native: Reader) -> None:
        super().__init__(stream, native)
        self._position: int | None = None
        self._carry: collections.deque[tuple[Any, int | None]] = collections.deque()
        self._events: ReaderEvents | None = None
        self._delivering = False
        self._generation = 0

    @property
    def mode(self) -> Mode:
        return MODE_NAMES[self._native.mode]

    @property
    def position(self) -> int | None:
        """The position of the last item received, None before the first.

        [`ReaderStatistics.position`][sharedbox.ReaderStatistics.position]
        is the position of the item received next, so right after a receive
        it is one more than this.
        """
        return self._position

    @property
    def missed(self) -> int:
        """Items a lossy or latest reader skipped; always 0 for a lossless reader."""
        return self._native.missed

    def receive(self, timeout: float | None = None) -> T:
        """Return the next item, with new arrays.

        Raises
        ------
        EndOfStream
            If the sender closed or died and every item it sent was received.
        TimeoutError
            If `timeout` seconds pass first.
        StreamClosedError
            If this reader is closed, before or during the wait.

        Notes
        -----
        A blocking receive first waits for calls already on the reader's
        background thread, such as an outstanding
        [`receive_future`][sharedbox.StreamReader.receive_future], and
        returns the item after theirs; that wait counts against `timeout`.
        An interrupt such as Ctrl-C during the call may lose the item being
        received.
        """
        self._check_consumer()
        return self._receive([], timeout)

    def receive_into(self, out: Any, /, *, timeout: float | None = None) -> T:
        """Return the next item, with its arrays read into the arrays in `out`.

        `out` has the item's shape: an array for an item that is an array, a
        tuple with an entry per member for a tuple item, and a mapping from
        member name to entry for a record item. An entry is an array, `None`
        for a member decoded as usual, or, for a member that is a record or a
        tuple, an `out` of its own shape. A name left out of a mapping is
        decoded as usual. The item returned holds your arrays, filled in
        place. Allocate the arrays once and pass them to every call:

            image = np.empty((480, 640), np.uint16)
            frame = reader.receive_into({"image": image})

        An item that a cancelled `receive_future` or `anext` had already
        received is returned by the next receive of any kind, and holds the
        arrays of the call that received it, not `out`.

        A member that is a list, set, frozenset, dict or `tuple[T, ...]`
        takes `None`, since an array cannot be a collection element, and an
        item that is a collection has no array to read into. Every check
        runs before anything is received, so a refused call takes no item.

        Raises
        ------
        TypeError
            If `out` does not have the item's shape, names a member the item
            does not have, gives an array for a member that is not an array,
            or holds an array that cannot be written; or if the item holds no
            array at all.
        ValueError
            If an array has another dtype or shape than its member.

        Notes
        -----
        A blocking receive first waits for calls already on the reader's
        background thread, such as an outstanding
        [`receive_future`][sharedbox.StreamReader.receive_future], and
        returns the item after theirs; that wait counts against `timeout`.
        After an exception the contents of `out` are unspecified. `out` must
        not be written to or resized from another thread during the call. An
        interrupt such as Ctrl-C during the call may lose the item being
        received.
        """
        self._check_consumer()
        return self._receive(self._targets(out), timeout)

    def receive_nowait(self) -> T:
        """Return the next item, or raise [`WouldBlock`][sharedbox.WouldBlock] if none is waiting.

        It also raises `WouldBlock` while a call on the reader's background
        thread is still running, such as a cancelled `anext` or a delivery
        stopping after the last `events.received` callback disconnected, so
        an item that call hands back is received first.
        """
        self._check_consumer()
        return self._take_nowait([])

    def receive_into_nowait(self, out: Any, /) -> T:
        """As [`receive_into`][sharedbox.StreamReader.receive_into], raising `WouldBlock` if no item is waiting."""
        self._check_consumer()
        return self._take_nowait(self._targets(out))

    def iter_into(self, out: Any, /) -> IterInto[T]:
        """Return an iterator of items read into the arrays in `out`, as `receive_into` takes it, for `for`."""
        self._check_consumer()
        return IterInto(self, self._targets(out))

    def __iter__(self) -> StreamReader[T]:
        return self

    def __next__(self) -> T:
        try:
            return self.receive()
        except EndOfStream:
            raise StopIteration from None

    def _targets(self, out: Any) -> list[tuple[tuple[int, ...], Any]]:
        item = self._stream._item
        if not item.has_arrays:
            raise TypeError(f"{item.label} holds no array; use receive()")
        return targets_of(item.spec, out, item.label)

    def _thread_role(self) -> str:
        return f"reader-{next(READER_IDS)}"

    def _check_consumer(self) -> None:
        if self._delivering:
            raise RuntimeError(
                "this reader delivers its items to reader.events; one reader cannot hand an item "
                "to two consumers, so open a second reader"
            )

    def _submit(self, call: Call) -> CallFuture:
        self._check_consumer()
        return super()._submit(call)

    @property
    def events(self) -> ReaderEvents:
        """The reader's psygnal signals: `received` as `(item, position)` for each item, and `ended` once per delivery.

        Connecting the first `received` callback starts delivery on the reader's
        background thread, and disconnecting the last one stops it. While
        delivery runs, every other way of receiving from this reader raises
        `RuntimeError`, also after the stream has ended, until the callbacks
        disconnect: one reader cannot hand an item to two consumers, so
        open a second reader for that. Callbacks run on the background
        thread; connect with `thread="main"` and call
        `psygnal.emit_queued()` to run them on the main thread. A callback
        that raises is logged to the `sharedbox` logger and delivery goes
        on, unless the receive itself fails: that is logged too, and callbacks
        then receive nothing more until a `received` callback connects or disconnects.
        Delivery runs only while `received` has a callback, and `ended` fires
        only then. A slow callback makes a lossy or latest
        reader miss items and holds the sender back for a lossless one.
        Right after delivery stops, the `_nowait` forms may raise `WouldBlock`
        until the delivering call has returned.

        Notes
        -----
        Starting and stopping delivery relies on psygnal calling the private
        methods `SignalInstance._append_slot` and `_remove_slot`.
        """
        with self._lock:
            if self._events is None:
                self._events = ReaderEvents(self)
            return self._events

    def _reconcile(self) -> None:
        """Start or stop delivery to match the `received` callbacks, until the two agree.

        Connects and disconnects can run on several threads, and psygnal calls
        this without its lock when a weakly referenced callback is collected,
        so the stop path takes no `End._lock` and the loop rechecks after
        acting.
        """
        events = self._events
        assert events is not None
        while (wanted := len(events.received) > 0) != self._delivering:
            if wanted:
                self._start_delivery()
            else:
                self._generation += 1
                self._delivering = False
                with contextlib.suppress(StreamClosedError):
                    self._native.interrupt()
            if self.closed:
                return

    def _start_delivery(self) -> None:
        with self._lock:
            if self._delivering or self.closed:
                return
            self._generation += 1
            generation = self._generation
            self._delivering = True
            try:
                self._submit_locked(lambda future: self._deliver(generation))
            except StreamClosedError:
                self._delivering = False

    def _worker_busy(self) -> bool:
        """Return whether a call runs or waits on the background thread and the caller is not that thread.

        Raises
        ------
        StreamClosedError
            If this reader is closed.
        """
        if self.closed:
            raise StreamClosedError(
                f"stream {self._stream.name!r}: the reader was closed"
            )
        worker = self._worker
        return self._inflight > 0 and not (
            worker is not None and worker.thread is threading.current_thread()
        )

    def _after_worker(self, timeout: float | None) -> float | None:
        """Wait until the background thread has no call left, so an item a call hands back comes first; return the time left.

        A finished future is not enough: the callback that keeps the item of
        a cancelled call runs after the future's waiters wake.
        """
        if not self._worker_busy():
            return timeout
        start = time.monotonic()
        with self._lock:
            for step in steps(timeout):
                if self._idle.wait_for(
                    lambda: self._inflight == 0 or self.closed, step
                ):
                    break
            else:
                raise TimeoutError(
                    f"stream {self._stream.name!r}: no item within {timeout} s"
                )
        return (
            None if timeout is None else max(0.0, timeout - (time.monotonic() - start))
        )

    def _stopped(self, generation: int) -> bool:
        return not self._delivering or self._generation != generation

    def _deliver(self, generation: int) -> None:
        events = self._events
        assert events is not None
        while not self._stopped(generation):
            before = self._position
            try:
                item = self._receive([], None, delivering=generation)
            except _Interrupted:
                return
            except EndOfStream:
                with events.received._lock:
                    if not self._stopped(generation):
                        self._emit(events.ended)
                return
            except StreamClosedError:
                return
            except Exception:
                logger.exception(
                    "delivery of stream %r stopped on an error", self._stream.name
                )
                if not self._stopped(generation):
                    self._generation += 1
                    self._delivering = False
                return
            position = self._position
            # SignalInstance._lock is a private psygnal name: disconnect holds it while it
            # stops delivery, so an item taken as delivery stops is either emitted to the
            # callbacks that were connected or handed back.
            with events.received._lock:
                if self._stopped(generation):
                    self._carry.appendleft((item, position))
                    self._position = before
                    return
                self._emit(events.received, item, position)

    def _emit(self, signal: SignalInstance, *args: Any) -> None:
        try:
            signal.emit(*args)
        except Exception:
            logger.exception("a callback of stream %r raised", self._stream.name)

    def _take_nowait(self, targets: list[tuple[tuple[int, ...], Any]]) -> T:
        if self._worker_busy():
            raise WouldBlock(f"stream {self._stream.name!r}: no item is waiting")
        try:
            return self._take(targets, 0.0)
        except _Interrupted:
            raise WouldBlock(
                f"stream {self._stream.name!r}: no item is waiting"
            ) from None

    def _take(
        self,
        targets: list[tuple[tuple[int, ...], Any]],
        step: float,
        future: CallFuture | None = None,
    ) -> T:
        with self._lock:
            if self.closed:
                raise StreamClosedError(
                    f"stream {self._stream.name!r}: the reader was closed"
                )
            if self._carry:
                item, position = self._carry.popleft()
                self._position = position
                if future is not None:
                    future.position = position
                return cast(T, item)
        item, position = self._native.receive(step, targets)
        self._position = position
        if future is not None:
            future.position = position
        return cast(T, item)

    def close(self) -> None:
        """Close this reader and drop items kept from cancelled receives."""
        self._delivering = False
        super().close()
        with self._lock:
            self._carry.clear()

    def _after_fork(self) -> None:
        super()._after_fork()
        self._carry.clear()
        self._delivering = False

    def _cancel(self, future: Future[Any]) -> None:
        super()._cancel(future)
        future.add_done_callback(self._keep)

    def _keep(self, future: Future[Any]) -> None:
        """Keep the item of a call whose awaiter gave up, for the next receive."""
        if not future.cancelled() and future.exception() is None:
            with self._lock:
                self._carry.append((future.result(), cast(CallFuture, future).position))

    def receive_future(self, out: Any = None, /) -> Future[T]:
        """Return a future of the next item, received by this reader's background thread.

        The future is a `concurrent.futures.Future`, so
        `concurrent.futures.wait` and `asyncio.wrap_future` take it. Its
        exception is [`EndOfStream`][sharedbox.EndOfStream] once the stream
        has ended, and [`StreamClosedError`][sharedbox.StreamClosedError] if
        the reader closes first. `out`, when given, is what
        [`receive_into`][sharedbox.StreamReader.receive_into] takes.

        Cancel the future before it starts to drop it. A running call cannot
        be cancelled: `cancel()` returns False, as for any `Future`, and the
        call goes on. An item it takes after its future was abandoned, or
        after an `asyncio.wrap_future` around it was cancelled, is lost. To
        wait on several readers, keep one outstanding future per reader and
        submit a new one only for the readers whose future completed.
        """
        return self._receive_future([] if out is None else self._targets(out))

    def _receive_future(self, targets: list[tuple[tuple[int, ...], Any]]) -> CallFuture:
        return self._submit(lambda future: self._receive(targets, None, future))

    def __aiter__(self) -> StreamReader[T]:
        return self

    async def __anext__(self) -> T:
        self._check_consumer()
        try:
            await self._yield()
            if self._fast():
                try:
                    return self._take_nowait([])
                except WouldBlock:
                    pass
            return cast(T, await self._wait(self._receive_future([])))
        except EndOfStream:
            raise StopAsyncIteration from None

    async def __aenter__(self) -> StreamReader[T]:
        return self

    def _receive(
        self,
        targets: list[tuple[tuple[int, ...], Any]],
        timeout: float | None,
        future: CallFuture | None = None,
        delivering: int | None = None,
    ) -> T:
        if future is None and delivering is None:
            timeout = self._after_worker(timeout)
        for step in steps(timeout):
            try:
                return self._take(targets, step, future)
            except WouldBlock:
                continue
            except _Interrupted:
                if future is not None and self._cancelled is future:
                    self._cancelled = None
                    raise CancelledError() from None
                if self.closed:
                    raise StreamClosedError(
                        f"stream {self._stream.name!r}: the reader was closed"
                    ) from None
                if delivering is not None and self._stopped(delivering):
                    raise
                continue
        raise TimeoutError(f"stream {self._stream.name!r}: no item within {timeout} s")

    def __enter__(self) -> StreamReader[T]:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __reduce__(
        self,
    ) -> tuple[Callable[..., StreamReader[Any]], tuple[object, str, Mode]]:
        return (open_reader, (self._stream._hint, self._stream.name, self.mode))


class IterInto(Iterator[T], AsyncIterator[T], Generic[T]):
    """Items of one reader read into the same arrays, for `for` loops.

    Notes
    -----
    A blocking `next` first waits for calls already on the reader's
    background thread, such as an outstanding
    [`receive_future`][sharedbox.StreamReader.receive_future], and returns
    the item after theirs.
    """

    __slots__ = ("_reader", "_targets")

    def __init__(
        self, reader: StreamReader[T], targets: list[tuple[tuple[int, ...], Any]]
    ) -> None:
        self._reader = reader
        self._targets = targets

    def __iter__(self) -> IterInto[T]:
        return self

    def __aiter__(self) -> IterInto[T]:
        return self

    async def __anext__(self) -> T:
        reader = self._reader
        reader._check_consumer()
        try:
            await reader._yield()
            if reader._fast():
                try:
                    return reader._take_nowait(self._targets)
                except WouldBlock:
                    pass
            return cast(T, await reader._wait(reader._receive_future(self._targets)))
        except EndOfStream:
            raise StopAsyncIteration from None

    def __next__(self) -> T:
        try:
            self._reader._check_consumer()
            return self._reader._receive(self._targets, None)
        except EndOfStream:
            raise StopIteration from None


def stop_stream_workers() -> None:
    """Close every stream end that has a worker thread, and wait for the threads, before the interpreter finalizes.

    A worker waiting in a native receive with the GIL released would
    otherwise take the GIL back during finalization. The wait lasts at most
    `EXIT_WAIT` seconds in all.
    """
    for end in list(LIVE_ENDS):
        if end._worker is not None:
            with contextlib.suppress(Exception):
                end.close()
    threads = list(WORKER_THREADS)
    deadline = time.monotonic() + EXIT_WAIT
    for thread in threads:
        if thread is not threading.current_thread():
            thread.join(max(0.0, deadline - time.monotonic()))
    late = [thread.name for thread in threads if thread.is_alive()]
    if late:
        logger.warning(
            "stream threads still running at exit after %s s: %s",
            EXIT_WAIT,
            ", ".join(late),
        )


atexit.register(stop_stream_workers)


def reset_streams_after_fork() -> None:
    """Make every stream end inherited through `fork` closed in the child, without touching the parent's."""
    WORKER_THREADS.clear()
    for end in list(LIVE_ENDS):
        end._after_fork()


if sys.platform != "win32":
    os.register_at_fork(after_in_child=reset_streams_after_fork)
