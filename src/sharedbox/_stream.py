"""SharedStream: a ring of typed items in shared memory, written by one sender and read by several readers."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import functools
import hashlib
import itertools
import logging
import os
import queue
import secrets
import sys
import threading
import time
import weakref
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Generator,
    Iterator,
    Mapping,
)
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from typing import (
    Any,
    Final,
    Generic,
    Literal,
    NoReturn,
    TypeVar,
    cast,
    overload,
)

from psygnal import Signal, SignalGroup, SignalInstance

from ._box import METADATA_CAPACITY, check_name
from ._metadata import DocumentCache, M, encode, newest
from ._native import (
    EndOfStream,
    Reader,
    ReaderReleased,
    SchemaMismatchError,
    Segment,
    Sender,
    Slot,
    Stream,
    StreamClosedError,
    Types,
    WouldBlock,
    _Interrupted,
)
from ._types import MAX_ARRAY_PLACES, Table, TypeSpec, misplaced_max_array, parse

T = TypeVar("T")
Mode = Literal["lossless", "lossy", "latest"]
Start = Literal["newest", "oldest"]
OnFull = Literal["wait", "release"]
MODES: Final[dict[str, int]] = {"lossless": 1, "lossy": 2, "latest": 3}
MODE_NAMES: Final[dict[int, Mode]] = {1: "lossless", 2: "lossy", 3: "latest"}
STEP: Final = 1.0
EXIT_WAIT: Final = 2.0
logger = logging.getLogger("sharedbox")
WORKER_THREADS: set[threading.Thread] = set()
READER_IDS = itertools.count(1)
YIELD_EVERY: Final = 64
YIELD_AFTER: Final = 0.001
# A slot's sequence number for item p is 2 * p + 2, which must fit in 64 bits.
MAX_POSITION: Final = 2**63 - 2
LIVE_ENDS: weakref.WeakSet[
    SharedSendStream[Any, Any] | SharedReceiveStream[Any, Any]
] = weakref.WeakSet()
LIVE_STREAMS: weakref.WeakSet[SharedStream[Any, Any]] = weakref.WeakSet()


@dataclass(frozen=True)
class Item:
    """How a stream stores its items, built once from the item annotation."""

    label: str
    types: Types
    entry: int
    """The item's `capacity_and_kind` word, as a box field table entry has."""
    schema_hash: int
    spec: TypeSpec
    """The parsed item type, which `receive_into` checks `out` against."""
    has_arrays: bool
    """Whether an array is reachable from the item through records and tuples."""


def item_of(hint: object, label: str | None = None) -> Item:
    """Return how a stream stores items annotated `hint`.

    Errors name `label`, by default the qualified name or repr of `hint`.

    Raises
    ------
    TypeError
        If a box field could not hold `hint`, or it names a box class.
    """
    if label is None:
        label = getattr(hint, "__qualname__", None) or repr(hint)
    spec = parse(hint, label)
    if misplaced_max_array(spec):
        raise TypeError(f"{label}: {MAX_ARRAY_PLACES}")
    table = Table()
    low = table.describe(spec) if spec.described else spec.entry_low
    if spec.bytearray:
        table.bytearrays.append((-1, 0))
    if spec.read_as is not None or spec.write_as is not None:
        table.converts.append((-1, 0, spec.read_as, spec.write_as))
    types = Types(
        [(0, low, spec.code)],
        [label],
        bytes(table.data),
        table.info,
        table.bytearrays,
        converts=table.converts,
        stream=True,
    )
    digest = hashlib.sha256(f"stream|{spec.text}".encode()).digest()
    return Item(
        label,
        types,
        low | spec.code << 24,
        int.from_bytes(digest[:8], "little"),
        spec,
        holds_array(spec),
    )


def holds_array(spec: TypeSpec) -> bool:
    """Return whether an array is reachable from `spec` through records and tuples."""
    match spec.kind:
        case "array" | "max_array":
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
        case "array" | "max_array":
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
class ReceiveStreamStatistics:
    """One open reader of a stream, as [`SharedStream.statistics`][sharedbox.SharedStream.statistics] found it."""

    mode: Mode
    position: int
    """The position of the item the reader receives next, at most `sent`.

    Right after a receive it is one more than
    [`SharedReceiveStream.position`][sharedbox.SharedReceiveStream.position],
    the position of the item received, except for the moment before the
    sender counts that item in `sent`.
    """
    lag: int
    """Items sent that the reader has not received or skipped yet."""
    pid: int


@dataclass(frozen=True)
class SharedStreamStatistics:
    """A stream's state at one moment: what was sent, what the ring holds, and who reads it."""

    capacity: int
    sent: int
    buffered: int
    """Items the ring holds: the last `capacity` sent, or fewer."""
    ended: bool
    sender_pid: int
    """The process holding the sender, 0 when none does."""
    readers: tuple[ReceiveStreamStatistics, ...]


class SharedStream(Generic[T, M]):
    """A ring of items in shared memory, sent by one process and received by up to `max_readers` readers.

    Create it with [`create`][sharedbox.SharedStream.create] in one process,
    and open it with [`attach`][sharedbox.SharedStream.attach] in others or
    pass it to them, as a process argument for instance: a pickled stream
    attaches again, and refuses a stream created since under the same name
    with [`SchemaMismatchError`][sharedbox.SchemaMismatchError]. Every item has the type given there: any type a
    `SharedBox` field takes, except a reference to a box.
    """

    __slots__ = (
        "__weakref__",
        "_documents",
        "_ends",
        "_hint",
        "_item",
        "_name",
        "_native",
        "_sender",
    )

    _documents: DocumentCache
    _hint: object
    _item: Item
    _name: str
    _native: Stream
    _ends: weakref.WeakSet[SharedSendStream[T, M] | SharedReceiveStream[T, M]]
    _sender: SharedSendStream[T, M] | None

    @overload
    @classmethod
    def create(
        cls,
        item_type: type[T],
        name: str | None = None,
        *,
        capacity: int,
        max_readers: int = 8,
        metadata: object = None,
        metadata_capacity: int | None = None,
        metadata_versions: int = 64,
        metadata_type: type[M] | None = None,
    ) -> SharedStream[T, M]: ...
    @overload
    @classmethod
    def create(
        cls,
        item_type: object,
        name: str | None = None,
        *,
        capacity: int,
        max_readers: int = 8,
        metadata: object = None,
        metadata_capacity: int | None = None,
        metadata_versions: int = 64,
        metadata_type: type[M] | None = None,
    ) -> SharedStream[Any, M]: ...
    @classmethod
    def create(
        cls,
        item_type: object,
        name: str | None = None,
        *,
        capacity: int,
        max_readers: int = 8,
        metadata: object = None,
        metadata_capacity: int | None = None,
        metadata_versions: int = 64,
        metadata_type: object = None,
    ) -> SharedStream[Any, Any]:
        """Create the stream `name` holding `capacity` items of `item_type`.

        Parameters
        ----------
        name
            Without one, the stream gets a new name of 16 hex digits. A
            process the stream is passed to opens it without being told the
            name, so only code that calls `attach` needs to know it.
        capacity
            Items the ring holds, at least 2. A lossless reader may fall
            this many items behind before `send` waits for it.
        max_readers
            Readers that may be open at once, 1 to 4095.
        metadata
            The stream's first metadata version, in force from the first
            item; see [`metadata`][sharedbox.SharedStream.metadata]. Its
            value follows the same rules as the `metadata` of
            [`send`][sharedbox.SharedSendStream.send].
        metadata_capacity
            Bytes of the stream's metadata area, 0 for none or at least 24.
            The area takes 64 bytes, plus 32 per version, plus the capacity
            rounded up to a multiple of 8, and must not pass 2**32 - 1
            bytes in all. A metadata document takes at most about a third
            of the capacity. By default a stream created with `metadata`
            gets 65536, and any other stream no area, so a send with
            `metadata` on its sender raises `MetadataFullError`.
        metadata_versions
            How many metadata versions the stream keeps at most, 2 to 65535.
        metadata_type
            The type of the stream's metadata documents, for type checkers.

        Raises
        ------
        SegmentExistsError
            If the name is taken.
        TypeError
            If a box field could not hold `item_type`, or `metadata` holds
            a value `send` refuses as `metadata`.
        ValueError
            If the name breaks the naming rules, `capacity` or
            `max_readers` is out of range, or `metadata`,
            `metadata_capacity` and `metadata_versions` make no metadata
            area that holds `metadata`.
        """
        item = item_of(item_type)
        if metadata_capacity is None:
            metadata_capacity = 0 if metadata is None else METADATA_CAPACITY
        native = Stream.create(
            secrets.token_hex(8) if name is None else check_name(name),
            item.types,
            item.entry,
            item.schema_hash,
            capacity,
            max_readers,
            metadata=None if metadata is None else encode(metadata),
            metadata_capacity=metadata_capacity,
            metadata_versions=metadata_versions,
        )
        return cls._wrap(item_type, item, native)

    @overload
    @classmethod
    def attach(
        cls,
        item_type: type[T],
        name: str,
        *,
        metadata_type: type[M] | None = None,
    ) -> SharedStream[T, M]: ...
    @overload
    @classmethod
    def attach(
        cls,
        item_type: object,
        name: str,
        *,
        metadata_type: type[M] | None = None,
    ) -> SharedStream[Any, M]: ...
    @classmethod
    def attach(
        cls, item_type: object, name: str, *, metadata_type: object = None
    ) -> SharedStream[Any, Any]:
        """Open the stream `name`, whose items must have the type `item_type`.

        Parameters
        ----------
        metadata_type
            The type of the stream's metadata documents, for type checkers.

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
    def _wrap(cls, hint: object, item: Item, native: Stream) -> SharedStream[Any, Any]:
        stream: SharedStream[Any, Any] = object.__new__(cls)
        stream._hint = hint
        stream._item = item
        stream._native = native
        stream._documents = DocumentCache()
        stream._name = native.name
        stream._ends = weakref.WeakSet()
        stream._sender = None
        LIVE_STREAMS.add(stream)
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

    @property
    def metadata(self) -> M | None:
        """The value of the stream's newest metadata version, or None if it has none.

        Metadata is a document stored beside the items, such as the units or
        settings they were taken with. The sender adds a version with the
        `metadata` of [`send`][sharedbox.SharedSendStream.send], in force
        from the item sent with it, and a reader's
        [`metadata`][sharedbox.SharedReceiveStream.metadata] gives the
        version of the item it returned last. The stream keeps the earlier
        versions until it reclaims them for newer ones, and
        [`metadata_version_of`][sharedbox.SharedStream.metadata_version_of]
        reads them until then. A mapping reads back as a read-only mapping
        and a list as a tuple, nested the same way. Each stream object
        decodes a version once and keeps the 32 it used last.

        Raises
        ------
        StreamClosedError
            If the stream is closed.
        """
        return newest(self._native.metadata_head, self.metadata_version_of)

    @property
    def metadata_version(self) -> int:
        """The number of the stream's newest metadata version, 0 if it has none.

        Raises
        ------
        StreamClosedError
            If the stream is closed.
        """
        return self._native.metadata_head()

    def metadata_version_of(self, version: int) -> M:
        """Return the value of metadata version `version`.

        Each stream object decodes a version once and keeps the 32 it used
        last.

        Raises
        ------
        MetadataGoneError
            If the stream no longer holds that version, or never did.
        StreamClosedError
            If the stream is closed.
        """
        # Raises for a reclaimed version, which the cache may still hold.
        self._native.metadata_length(version)
        return cast(
            M,
            self._documents.get(
                self._native.create_id,
                version,
                functools.partial(self._native.read_metadata, version),
            ),
        )

    def sender(self, on_full: OnFull = "wait") -> SharedSendStream[T, M]:
        """Return the stream's sender for this process.

        Parameters
        ----------
        on_full
            What a send does when its `timeout` passes while a lossless
            reader is a full ring behind. `"wait"` raises `TimeoutError`.
            `"release"` releases the readers that hold it back and sends:
            each raises [`ReaderReleased`][sharedbox.ReaderReleased] from its
            next receive, then goes on at the newest item. A reader from
            sharedbox 0.7.0 or earlier cannot be released, so a send it holds
            back still raises `TimeoutError`.

        Raises
        ------
        ValueError
            If `on_full` is neither `"wait"` nor `"release"`.
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
        if on_full not in ("wait", "release"):
            raise ValueError(f"on_full is 'wait' or 'release'; got {on_full!r}")
        end: SharedSendStream[T, M] = SharedSendStream(
            self, self._native.sender(), on_full == "release"
        )
        self._ends.add(end)
        self._sender = end
        return end

    def reader(
        self, mode: Mode = "lossless", start: Start = "newest"
    ) -> SharedReceiveStream[T, M]:
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
        end: SharedReceiveStream[T, M] = SharedReceiveStream(
            self, self._native.reader(MODES[mode], start == "newest")
        )
        self._ends.add(end)
        return end

    def statistics(self) -> SharedStreamStatistics:
        """Return what was sent, what the ring holds, and each open reader's state."""
        sent, ended, sender_pid, readers = self._native.statistics()
        return SharedStreamStatistics(
            self.capacity,
            sent,
            min(sent, self.capacity),
            ended,
            sender_pid,
            tuple(
                ReceiveStreamStatistics(
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

    def __enter__(self) -> SharedStream[T, M]:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __reduce__(
        self,
    ) -> tuple[Callable[..., SharedStream[Any, Any]], tuple[object, str, int]]:
        return (attach_stream, (self._hint, self.name, self._native.create_id))

    def __repr__(self) -> str:
        state = " closed" if self.closed else ""
        return f"<SharedStream[{self._item.label}] {self.name!r}{state}>"


def attach_stream(
    item_type: object, name: str, create_id: int
) -> SharedStream[Any, Any]:
    """Attach to the stream a pickle refers to, if it is still the stream that was pickled.

    Raises
    ------
    SchemaMismatchError
        If the stream under `name` was created after the pickle was taken.
    """
    stream = SharedStream.attach(item_type, name)
    if stream._native.create_id != create_id:
        stream.close()
        raise SchemaMismatchError(
            f"a SharedStream was pickled from a different stream named {name!r}"
        )
    return stream


class CallFuture(Future[Any]):
    """The future of one call on a worker; `position` is where a receive stopped.

    `on_cancel`, when set, is called by a `cancel()` that finds the call
    already running, to interrupt it.
    """

    position: int | None = None
    on_cancel: Callable[[CallFuture], None] | None = None

    def cancel(self) -> bool:
        if super().cancel():
            return True
        if self.on_cancel is not None and not self.done():
            self.on_cancel(self)
        return False


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

    def __init__(self, stream: SharedStream[Any, Any], native: Sender | Reader) -> None:
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
        return self._shut

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
        self._interrupt_call(future)

    def _interrupt_call(self, future: Future[Any]) -> None:
        """End the wait of the running call behind `future`, which then raises `CancelledError`."""
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


def check_position(position: int) -> None:
    """Raise `ValueError` for a position no item can have."""
    if not 0 <= position <= MAX_POSITION:
        raise ValueError(f"a position is 0 to {MAX_POSITION}, not {position}")


def holder() -> object:
    """Return what a slot reserved now belongs to: the running asyncio task, or else the thread."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident() if task is None else task


class SharedSendStream(End, Generic[T, M]):
    """The sender of a stream: one per stream, in the process that holds it.

    [`SharedStream.sender`][sharedbox.SharedStream.sender] returns it. The
    stream keeps it open until `close()`, or until the stream is closed or
    the process dies; dropping the object does not close it.
    Closing it ends the stream: readers receive what is buffered, then
    [`EndOfStream`][sharedbox.EndOfStream].
    """

    __slots__ = ("_holders", "_release")
    _native: Sender
    _holders: set[object]

    def __init__(
        self, stream: SharedStream[Any, Any], native: Sender, release: bool = False
    ) -> None:
        super().__init__(stream, native)
        self._holders = set()
        self._release = release

    @property
    def stream(self) -> SharedStream[T, M]:
        """The stream this sender sends to."""
        return self._stream

    def _thread_role(self) -> str:
        return "sender"

    def send(
        self, item: T, timeout: float | None = None, *, metadata: M | None = None
    ) -> None:
        """Send `item`, waiting while a lossless reader is a full ring behind.

        The item is encoded straight into its slot once the slot is free, so
        an item that cannot be encoded raises only after that wait.

        With `metadata`, the item is sent with a new metadata version, in
        force from this item on: a reader that receives it or a later one
        sees the version in
        [`metadata`][sharedbox.SharedReceiveStream.metadata]. The value is
        `None`, a `bool`, an `int` from -2**64 to 2**64 - 1, a `float`, a
        `str`, `bytes`, a mapping with `str` keys, or a `list` or `tuple`,
        nested up to 32 levels; `metadata=None` adds no version. A document
        the stream cannot store raises before the send waits, and when
        either the document or the item is refused, neither is stored. The
        oldest versions are reclaimed once the stream holds
        `metadata_versions` of them, or needs their room in its metadata
        area, but never while an item still in the ring was sent under one;
        version 1 is also kept while an item sent before it is in the ring.

        With `on_full="release"`, a send that has waited `timeout` seconds
        releases the lossless readers that hold it back and sends, instead of
        raising `TimeoutError`. A `timeout` of None never passes, so it never
        releases.

        Raises
        ------
        TimeoutError
            If `timeout` seconds pass first; the item is not sent. With
            `on_full="release"`, only if another thread or task holds the
            slot from `writing`, or a reader from sharedbox 0.7.0 or earlier
            holds the sender back.
        StreamClosedError
            If this sender is closed, before or during the wait.
        RuntimeError
            If called inside a block of this sender's
            [`writing`][sharedbox.SharedSendStream.writing].
        TypeError
            If `metadata` holds another type, or a mapping key is not a
            `str`.
        ValueError
            If an `int` in `metadata` is out of range, the nesting is deeper
            than 32 levels, or the document is longer than the metadata area
            takes, about a third of the stream's `metadata_capacity`.
        MetadataFullError
            If `metadata` is given and the stream has no metadata area, or
            the new version would reclaim one that an item still in the ring
            was sent under, or version 1 while an item sent before it is in
            the ring; the item is not sent. A send after enough further sends
            succeeds.

        Notes
        -----
        An interrupt such as Ctrl-C during the call may leave the item sent
        although the call raised.
        """
        self._refuse_nested()
        if metadata is None:
            self._send(item, timeout)
        else:
            self._retry(
                self._native.send_metadata, timeout, None, encode(metadata), item
            )

    async def asend(
        self, item: T, timeout: float | None = None, *, metadata: M | None = None
    ) -> None:
        """Send `item`, as [`send`][sharedbox.SharedSendStream.send] does, without blocking the event loop.

        `metadata` has the meaning it has in `send`, and so does `timeout`,
        including the release of readers under `on_full="release"`. An
        `asend` cancelled before its `timeout` passes releases nothing, so a
        sender that must not stall passes a `timeout` instead of wrapping
        the call in `asyncio.wait_for`.

        A cancelled `asend` may still have sent its item, if a reader made
        room just as it was cancelled.

        Raises
        ------
        TimeoutError
            If `timeout` seconds pass first; the item is not sent.
        StreamClosedError
            If this sender is closed, before or during the wait.
        RuntimeError
            If called inside a block of this sender's `writing`.
        TypeError
            If `metadata` holds a value `send` refuses.
        ValueError
            If `metadata` does not fit, as in `send`.
        MetadataFullError
            If `metadata` is given and the stream cannot take a new version
            now, as in `send`; the item is not sent.
        """
        self._refuse_nested()
        if metadata is not None:
            await self._asend_metadata(item, timeout, encode(metadata))
            return
        await self._yield()
        if self._fast():
            try:
                self._send_nowait(item, False)
                return
            except WouldBlock:
                pass
        await self._wait(self._submit(lambda future: self._send(item, timeout, future)))

    async def _asend_metadata(self, item: T, timeout: float | None, doc: bytes) -> None:
        await self._yield()
        if self._fast():
            try:
                self._send_nowait_metadata(doc, item, False)
                return
            except WouldBlock:
                pass
        await self._wait(
            self._submit(
                lambda future: self._retry(
                    self._native.send_metadata, timeout, future, doc, item
                )
            )
        )

    def send_nowait(self, item: T, *, metadata: M | None = None) -> None:
        """Send `item`, or raise [`WouldBlock`][sharedbox.WouldBlock] if that needs a wait.

        It would wait while a lossless reader is a full ring behind, or while
        another thread or task holds the slot from
        [`writing`][sharedbox.SharedSendStream.writing] or
        [`awriting`][sharedbox.SharedSendStream.awriting].

        With `on_full="release"`, a lossless reader that is a full ring
        behind is released at once and the item is sent. The call still
        raises `WouldBlock` while another thread or task holds the slot, and
        while a reader from sharedbox 0.7.0 or earlier holds the sender back.

        `metadata` has the meaning it has in
        [`send`][sharedbox.SharedSendStream.send]; a document the stream
        cannot store raises before `WouldBlock`.

        Raises
        ------
        RuntimeError
            If called inside a block of this sender's `writing`.
        TypeError
            If `metadata` holds a value `send` refuses.
        ValueError
            If `metadata` does not fit, as in `send`.
        MetadataFullError
            If `metadata` is given and the stream cannot take a new version
            now, as in `send`; the item is not sent.
        """
        self._refuse_nested()
        if metadata is None:
            self._send_nowait(item, self._release)
        else:
            self._send_nowait_metadata(encode(metadata), item, self._release)

    def _send_nowait(self, item: T, release: bool) -> None:
        try:
            self._native.send(item, 0.0, release)
        except _Interrupted:
            raise WouldBlock(
                f"stream {self._stream.name!r}: a lossless reader is a full ring behind"
            ) from None

    def _send_nowait_metadata(self, doc: bytes, item: T, release: bool) -> None:
        try:
            self._native.send_metadata(doc, item, 0.0, release)
        except _Interrupted:
            raise WouldBlock(
                f"stream {self._stream.name!r}: a lossless reader is a full ring behind"
            ) from None

    def _refuse_nested(self) -> None:
        """Raise RuntimeError when called inside a `writing` block of this sender, whose slot a send would wait for."""
        if self._holders and holder() in self._holders:
            raise RuntimeError(
                f"stream {self._stream.name!r}: a send inside this sender's "
                "writing block is refused"
            )

    def _retry(
        self,
        call: Callable[..., object],
        timeout: float | None,
        future: CallFuture | None,
        *args: object,
    ) -> None:
        if future is None:
            # A free slot needs no wait; without one, the loop below waits as before.
            try:
                call(*args, 0.0)
                return
            except (WouldBlock, _Interrupted):
                pass
        for step in steps(timeout):
            try:
                call(*args, step)
                return
            except WouldBlock:
                # A wait for another thread's slot is not interrupted, so a cancel is seen at the end of a step.
                if future is not None and self._cancelled is future:
                    self._cancelled = None
                    raise CancelledError() from None
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
        if self._release:
            if future is not None and self._cancelled is future:
                self._cancelled = None
                raise CancelledError()
            # The step loop ends only once the whole timeout has passed, so the
            # release attempt comes after it.
            try:
                call(*args, 0.0, True)
                return
            except WouldBlock:
                pass
            except _Interrupted:
                if future is not None and self._cancelled is future:
                    self._cancelled = None
                    raise CancelledError() from None
                if self.closed:
                    raise StreamClosedError(
                        f"stream {self._stream.name!r}: the sender was closed"
                    ) from None
        raise TimeoutError(
            f"stream {self._stream.name!r}: no slot became free within {timeout} s"
        )

    def _send(
        self, item: T, timeout: float | None, future: CallFuture | None = None
    ) -> None:
        self._retry(self._native.send, timeout, future, item)

    def _reserve(self, timeout: float | None, future: CallFuture | None = None) -> None:
        self._retry(self._native.reserve, timeout, future)

    @contextlib.contextmanager
    def writing(
        self, template: T | None = None, timeout: float | None = None
    ) -> Generator[SendSlot[T, M], None, None]:
        """Reserve the next slot and yield it, to fill the item's arrays in place and send it.

        [`slot.item`][sharedbox.SendSlot.item] is an item built from
        `template`, with each array it reaches through records and tuples
        replaced by a writable array over the slot. The template's own
        arrays are never read, so an array with a `Shape` may be empty. An
        array with a [`MaxShape`][sharedbox.MaxShape] gives only the shape
        of the array over the slot, so `np.empty(shape)` serves. The block
        fills the slot's arrays and sends the item with
        [`slot.send`][sharedbox.SendSlot.send], which does not copy them:

        ```python
        blank = Letter(0, "", np.empty((0, 0), np.uint8))
        with sender.writing(blank) as slot:
            scanner.read_into(slot.item.scan)
            slot.send(replace(slot.item, index=n))
        ```

        An item that is an array needs no template, and the array over the
        slot then has the maximum shape for a `MaxShape` item. Leaving the block
        without `send`, or by an exception, gives the slot up: nothing is
        published, and the next send takes its position. Until the slot is
        sent or the block ends, readers see nothing new, and a send or
        `writing` on this sender from another thread or task waits. That
        includes a task started inside the block by `asyncio.wait_for` or
        `create_task`.

        With `on_full="release"`, the wait for a free slot ends after
        `timeout` seconds by releasing the lossless readers that hold it
        back, as in [`send`][sharedbox.SharedSendStream.send].

        Raises
        ------
        TypeError
            If `template` is `None` and the item is not an array, or
            `template` is not of the item type.
        ValueError
            If a value of `template` outside its arrays does not fit, or an
            array of `template` has a shape beyond its `MaxShape`.
        TimeoutError
            If `timeout` seconds pass while a lossless reader is a full ring
            behind and the sender does not release it, or while another
            thread or task holds the slot from `writing` or `awriting`.
        StreamClosedError
            If this sender is closed, before or during the wait.
        RuntimeError
            If called inside a block of this sender's `writing`.

        Notes
        -----
        An interrupt such as Ctrl-C just before the block starts may leave
        the slot reserved with no block to give it up, so later sends on
        this sender wait until they time out. Closing the sender ends them.
        """
        self._refuse_nested()
        self._reserve(timeout)
        with self._held(template) as slot:
            yield slot

    @contextlib.asynccontextmanager
    async def awriting(
        self, template: T | None = None, timeout: float | None = None
    ) -> AsyncGenerator[SendSlot[T, M], None]:
        """Reserve the next slot, as [`writing`][sharedbox.SharedSendStream.writing] does, without blocking the event loop.

        `timeout` has the meaning it has in `writing`. An `awriting` cancelled
        before its `timeout` passes reserves nothing and releases no reader.

        Raises
        ------
        TypeError
            If `template` is `None` and the item is not an array, or
            `template` is not of the item type.
        TimeoutError
            If `timeout` seconds pass first.
        StreamClosedError
            If this sender is closed, before or during the wait.
        RuntimeError
            If called inside a block of this sender's `writing`.
        """
        self._refuse_nested()
        await self._yield()
        if not (self._fast() and self._reserve_nowait()):
            future = self._submit(lambda future: self._reserve(timeout, future))
            try:
                await asyncio.wrap_future(future)
            except asyncio.CancelledError:
                self._cancel(future)
                future.add_done_callback(self._abandon_reserved)
                raise
        with self._held(template) as slot:
            yield slot

    def _reserve_nowait(self) -> bool:
        """Reserve the next slot if that needs no wait, and return whether it did."""
        try:
            self._native.reserve(0.0)
        except (WouldBlock, _Interrupted):
            return False
        return True

    def _abandon_reserved(self, future: Future[Any]) -> None:
        """Give up the slot that a cancelled `awriting` reserved after all."""
        if not future.cancelled() and future.exception() is None:
            self._native.abandon()

    @contextlib.contextmanager
    def _held(self, template: T | None) -> Generator[SendSlot[T, M], None, None]:
        """Yield the reserved slot with its item built from `template`, and give it up at the end unless it was sent."""
        try:
            slot: SendSlot[T, M] = SendSlot(self._native, template)
        except BaseException:
            self._native.abandon()
            raise
        me = holder()
        with self._lock:
            self._holders.add(me)
        try:
            yield slot
        finally:
            with self._lock:
                self._holders.discard(me)
            slot.abandon()

    def _after_fork(self) -> None:
        super()._after_fork()
        self._holders = set()

    def close(self) -> None:
        """Close the sender and end the stream. Calling it again does nothing."""
        super().close()
        if self._stream._sender is self:
            self._stream._sender = None

    def __enter__(self) -> SharedSendStream[T, M]:
        return self

    async def __aenter__(self) -> SharedSendStream[T, M]:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __reduce__(self) -> NoReturn:
        raise TypeError(
            "a SharedSendStream cannot be pickled; "
            "send its stream and call sender() in the other process"
        )


class SendSlot(Slot[T, M]):
    """A slot of a stream that [`SharedSendStream.writing`][sharedbox.SharedSendStream.writing] reserved for one item.

    [`writing`][sharedbox.SharedSendStream.writing] and
    [`awriting`][sharedbox.SharedSendStream.awriting] make it. Its
    [`item`][sharedbox.SendSlot.item] holds arrays over the slot, which
    the block fills, and [`send`][sharedbox.SendSlot.send] publishes an
    item.
    """

    __slots__ = ()


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
        if isinstance(group, ReceiveEvents):
            group._sharedbox_changed()


class ReceiveEvents(SignalGroup):
    """The psygnal signal group of a stream reader.

    It is the type of [`SharedReceiveStream.events`][sharedbox.SharedReceiveStream.events].
    """

    received = Signal(object, int, signal_instance_class=CountedSignal)
    """Emitted as `(item, position)` for each item the reader receives."""
    ended = Signal()
    """Emitted once per delivery, when it reaches the end of the stream."""
    released = Signal(int)
    """Emitted as `(missed,)` when the sender released this lossless reader; delivery goes on at the newest item."""

    def __init__(self, reader: SharedReceiveStream[Any, Any] | None = None) -> None:
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


class SharedReceiveStream(End, Iterator[T], AsyncIterator[T], Generic[T, M]):
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
        "_documents",
        "_events",
        "_generation",
        "_position",
    )
    _native: Reader

    def __init__(self, stream: SharedStream[Any, Any], native: Reader) -> None:
        super().__init__(stream, native)
        self._documents = DocumentCache()
        self._position: int | None = None
        self._carry: collections.deque[tuple[Any, int | None] | ReaderReleased] = (
            collections.deque()
        )
        self._events: ReceiveEvents | None = None
        self._delivering = False
        self._generation = 0

    @property
    def stream(self) -> SharedStream[T, M]:
        """The stream this reader receives from."""
        return self._stream

    @property
    def mode(self) -> Mode:
        return MODE_NAMES[self._native.mode]

    @property
    def position(self) -> int | None:
        """The position of the item this reader returned last, by a receive or a fetch, None before the first.

        [`ReceiveStreamStatistics.position`][sharedbox.ReceiveStreamStatistics.position]
        is the position of the item received next, so right after a receive
        it is one more than this.
        """
        return self._position

    @property
    def missed(self) -> int:
        """Items a lossy or latest reader skipped, or a lossless reader lost to releases."""
        return self._native.missed

    @property
    def metadata_version(self) -> int:
        """The metadata version the item this reader returned last was sent under.

        The item is the last one a receive or a fetch returned. The version
        is the newest one the sender added before it sent that item, and 0
        before the first item, or for an item sent before the stream had any
        version.

        Raises
        ------
        MetadataGoneError
            If the stream no longer holds that version. A version can be
            reclaimed once the item this reader returned last has left the
            ring, which for a lossy or latest reader can happen right after
            the receive.
        StreamClosedError
            If this reader is closed.
        """
        if self.closed:
            raise StreamClosedError(
                f"stream {self._stream.name!r}: the reader was closed"
            )
        position = self._position
        return 0 if position is None else self._native.metadata_version_at(position)

    @property
    def metadata(self) -> M | None:
        """The value of [`metadata_version`][sharedbox.SharedReceiveStream.metadata_version], None while it is 0.

        A mapping reads back as a read-only mapping and a list as a tuple,
        nested the same way. Each reader decodes a version once and keeps
        the 32 it used last.

        Raises
        ------
        MetadataGoneError
            If the stream no longer holds that version.
        StreamClosedError
            If this reader is closed.
        """
        version = self.metadata_version
        if version == 0:
            return None
        return cast(
            M,
            self._documents.get(
                self._stream._native.create_id,
                version,
                functools.partial(self._stream._native.read_metadata, version),
            ),
        )

    def receive(self, timeout: float | None = None) -> T:
        """Return the next item, with new arrays.

        Raises
        ------
        ReaderReleased
            If the sender released this lossless reader; the next receive
            returns the newest item.
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
        [`receive_future`][sharedbox.SharedReceiveStream.receive_future], and
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

            scan = np.empty((32, 32), np.uint8)
            letter = reader.receive_into({"scan": scan})

        An entry for a member with a [`MaxShape`][sharedbox.MaxShape] has
        the maximum shape. The item then holds a view of the stored shape
        over the entry's first elements in C order, not the entry itself.
        Those elements are the entry's first rows only when every dimension
        but the first is at its maximum.

        An item that a cancelled `receive_future` or `anext` had already
        received is returned by the next receive of any kind, and holds the
        arrays of the call that received it, not `out`.

        A member that is a list, set, frozenset, dict or `tuple[T, ...]`
        takes `None`, since an array cannot be a collection element, and an
        item that is a collection has no array to read into. Every check
        runs before anything is received, so a refused call takes no item.

        Raises
        ------
        ReaderReleased
            If the sender released this lossless reader; the next receive
            returns the newest item.
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
        [`receive_future`][sharedbox.SharedReceiveStream.receive_future], and
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

        Raises
        ------
        ReaderReleased
            If the sender released this lossless reader; the next receive
            returns the newest item.
        """
        self._check_consumer()
        return self._take_nowait([])

    def receive_into_nowait(self, out: Any, /) -> T:
        """As [`receive_into`][sharedbox.SharedReceiveStream.receive_into], raising `WouldBlock` if no item is waiting.

        Raises
        ------
        ReaderReleased
            If the sender released this lossless reader; the next receive
            returns the newest item.
        """
        self._check_consumer()
        return self._take_nowait(self._targets(out))

    def iter_into(self, out: Any, /) -> IterInto[T]:
        """Return an iterator of items read into the arrays in `out`, as `receive_into` takes it, for `for`.

        It raises [`ReaderReleased`][sharedbox.ReaderReleased] as `receive` does.
        """
        self._check_consumer()
        return IterInto(self, self._targets(out))

    def __iter__(self) -> SharedReceiveStream[T, M]:
        return self

    def __next__(self) -> T:
        try:
            return self.receive()
        except EndOfStream:
            raise StopIteration from None

    def _targets(self, out: Any) -> list[tuple[tuple[int, ...], Any]]:
        item = self._stream._item
        if not item.has_arrays:
            # An array given for a member, such as an optional array, gets the member's own message.
            targets_of(item.spec, out, item.label)
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
    def events(self) -> ReceiveEvents:
        """The reader's psygnal signals: `received` as `(item, position)` for each item, `released` as `(missed,)` when the sender releases the reader, and `ended` once per delivery.

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
                self._events = ReceiveEvents(self)
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
            except ReaderReleased as released:
                with events.received._lock:
                    if self._stopped(generation):
                        self._carry.appendleft(released)
                        return
                    self._emit(events.released, released.missed)
                continue
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
        position: int | None = None,
    ) -> T:
        with self._lock:
            if self.closed:
                raise StreamClosedError(
                    f"stream {self._stream.name!r}: the reader was closed"
                )
            # Items kept from cancelled receives belong to the receive cursor, which a fetch leaves alone.
            if self._carry and position is None:
                kept = self._carry.popleft()
                if isinstance(kept, ReaderReleased):
                    raise kept
                item, position = kept
                self._position = position
                if future is not None:
                    future.position = position
                return cast(T, item)
        item, at = (
            self._native.receive(step, targets)
            if position is None
            else self._native.fetch(position, step, targets)
        )
        self._position = at
        if future is not None:
            future.position = at
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
        """Keep the item or the release of a call whose awaiter gave up, for the next receive."""
        if future.cancelled():
            return
        error = future.exception()
        if error is None:
            with self._lock:
                self._carry.append((future.result(), cast(CallFuture, future).position))
        elif isinstance(error, ReaderReleased):
            with self._lock:
                self._carry.append(error)

    def receive_future(self, out: Any = None, /) -> Future[T]:
        """Return a future of the next item, received by this reader's background thread.

        The future is a `concurrent.futures.Future`, so
        `concurrent.futures.wait` and `asyncio.wrap_future` take it. Its
        exception is [`EndOfStream`][sharedbox.EndOfStream] once the stream
        has ended, [`ReaderReleased`][sharedbox.ReaderReleased] if the sender
        released the reader, and
        [`StreamClosedError`][sharedbox.StreamClosedError] if
        the reader closes first. `out`, when given, is what
        [`receive_into`][sharedbox.SharedReceiveStream.receive_into] takes.

        Cancel the future before it starts to drop it. A running call cannot
        be cancelled: `cancel()` returns False, as for any `Future`, and the
        call goes on. An item it takes after its future was abandoned, or
        after an `asyncio.wrap_future` around it was cancelled, is lost. To
        wait on several readers, keep one outstanding future per reader and
        submit a new one only for the readers whose future completed.
        """
        return self._receive_future([] if out is None else self._targets(out))

    def fetch(self, position: int, /, timeout: float | None = None) -> T:
        """Return the item at `position`, with new arrays, once the sender has sent it.

        A fetch leaves the receive cursor and
        [`missed`][sharedbox.SharedReceiveStream.missed] alone, so the next
        [`receive`][sharedbox.SharedReceiveStream.receive] returns what it
        would have returned without it.
        [`position`][sharedbox.SharedReceiveStream.position] and
        [`metadata`][sharedbox.SharedReceiveStream.metadata] then describe the
        fetched item. An item the ring still holds is returned at once, and
        `timeout=0` does not wait for one not sent yet.

        Raises
        ------
        ItemGoneError
            If the ring no longer holds the item, because the sender wrote a
            later item over it before or during the copy.
        EndOfStream
            If the sender closed or died without sending the item.
        TimeoutError
            If `timeout` seconds pass first.
        StreamClosedError
            If this reader is closed, before or during the wait.
        ValueError
            If `position` is negative or above `2**63 - 2`.

        Notes
        -----
        A blocking fetch first waits for calls already on the reader's
        background thread, and that wait counts against `timeout`. A
        lossless reader still holds the sender back from its receive
        cursor, so a fetch more than `capacity` items past the cursor waits
        until a receive moves it; open a reader that only fetches as lossy.
        """
        self._check_consumer()
        check_position(position)
        return self._receive([], timeout, position=position)

    def fetch_into(
        self, position: int, out: Any, /, *, timeout: float | None = None
    ) -> T:
        """As [`fetch`][sharedbox.SharedReceiveStream.fetch], with the item's arrays read into the arrays in `out`.

        `out` has the shape that
        [`receive_into`][sharedbox.SharedReceiveStream.receive_into] takes.
        After an exception the contents of `out` are unspecified.

        Raises
        ------
        ItemGoneError
            If the ring no longer holds the item.
        EndOfStream
            If the sender closed or died without sending the item.
        TimeoutError
            If `timeout` seconds pass first.
        StreamClosedError
            If this reader is closed, before or during the wait.
        ValueError
            If `position` is negative or above `2**63 - 2`.
        """
        self._check_consumer()
        check_position(position)
        return self._receive(self._targets(out), timeout, position=position)

    def fetch_future(self, position: int, out: Any = None, /) -> Future[T]:
        """Return a future of the item at `position`, fetched by this reader's background thread.

        The future is a `concurrent.futures.Future`, as
        [`receive_future`][sharedbox.SharedReceiveStream.receive_future]
        returns. Its exception is [`ItemGoneError`][sharedbox.ItemGoneError],
        [`EndOfStream`][sharedbox.EndOfStream] or
        [`StreamClosedError`][sharedbox.StreamClosedError] in the cases where
        [`fetch`][sharedbox.SharedReceiveStream.fetch] raises them. `out`,
        when given, is what
        [`fetch_into`][sharedbox.SharedReceiveStream.fetch_into] takes.
        Cancelling the future stops the fetch, even once it is running, so a
        fetch given up on, such as one an `asyncio.wait_for` timed out, does
        not hold up later calls on the reader. `cancel()` returns False for a
        running fetch, as for any `Future`, and the future then ends with
        `CancelledError`.

        Raises
        ------
        ValueError
            If `position` is negative or above `2**63 - 2`.
        """
        check_position(position)
        targets = [] if out is None else self._targets(out)
        future = self._submit(
            lambda future: self._receive(targets, None, future, position=position)
        )
        # A fetch takes nothing from the cursor, so stopping it mid-wait loses no item.
        future.on_cancel = self._interrupt_call
        return future

    def _receive_future(self, targets: list[tuple[tuple[int, ...], Any]]) -> CallFuture:
        return self._submit(lambda future: self._receive(targets, None, future))

    def __aiter__(self) -> SharedReceiveStream[T, M]:
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

    async def __aenter__(self) -> SharedReceiveStream[T, M]:
        return self

    def _receive(
        self,
        targets: list[tuple[tuple[int, ...], Any]],
        timeout: float | None,
        future: CallFuture | None = None,
        delivering: int | None = None,
        position: int | None = None,
    ) -> T:
        if future is None and delivering is None:
            timeout = self._after_worker(timeout)
            # A waiting item needs no wait; without one, the loop below waits as before.
            try:
                return self._take(targets, 0.0, None, position)
            except (WouldBlock, _Interrupted):
                pass
        for step in steps(timeout):
            try:
                return self._take(targets, step, future, position)
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

    def __enter__(self) -> SharedReceiveStream[T, M]:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __reduce__(self) -> NoReturn:
        raise TypeError(
            "a SharedReceiveStream cannot be pickled; "
            "send its stream and call reader() in the other process"
        )


class IterInto(Iterator[T], AsyncIterator[T], Generic[T]):
    """Items of one reader read into the same arrays, for `for` loops.

    Notes
    -----
    A blocking `next` first waits for calls already on the reader's
    background thread, such as an outstanding
    [`receive_future`][sharedbox.SharedReceiveStream.receive_future], and
    returns the item after theirs.
    """

    __slots__ = ("_reader", "_targets")

    def __init__(
        self,
        reader: SharedReceiveStream[T, Any],
        targets: list[tuple[tuple[int, ...], Any]],
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


def reset_streams_after_fork() -> None:
    """Make every stream end inherited through `fork` closed in the child, without touching the parent's."""
    WORKER_THREADS.clear()
    for end in list(LIVE_ENDS):
        end._after_fork()
    for stream in list(LIVE_STREAMS):
        # Another thread of the parent may have held its lock at the fork.
        stream._documents = DocumentCache()


if sys.platform != "win32":
    os.register_at_fork(after_in_child=reset_streams_after_fork)
