"""SharedStream: a ring of typed items in shared memory, written by one sender and read by several readers."""

from __future__ import annotations

import hashlib
import threading
import time
import weakref
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Final, Generic, Literal, TypeVar, cast, overload

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
        case _:
            raise TypeError(
                f"{label} holds no array to read into, since an array cannot be part of a "
                f"{spec.kind}; give None for it or leave it out"
            )


@dataclass(frozen=True)
class ReaderStatistics:
    """One open reader of a stream, as [`SharedStream.statistics`][sharedbox.SharedStream.statistics] found it."""

    mode: Mode
    position: int
    """The position the reader receives next."""
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

    __slots__ = ("__weakref__", "_ends", "_hint", "_item", "_name", "_native")

    _hint: object
    _item: Item
    _name: str
    _native: Stream
    _ends: weakref.WeakSet[StreamSender[T] | StreamReader[T]]

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
            If another live end holds the sender. A sender whose process
            died is replaced.
        EndOfStream
            If a sender already closed the stream.
        """
        end: StreamSender[T] = StreamSender(self, self._native.sender())
        self._ends.add(end)
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
        WaiterSlotsFullError
            If `max_readers` readers are open.
        """
        if mode not in MODES:
            raise ValueError(f"mode is 'lossless', 'lossy' or 'latest'; got {mode!r}")
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
                ReaderStatistics(MODE_NAMES[mode], position, sent - position, pid)
                for position, mode, pid in readers
            ),
        )

    def close(self) -> None:
        """Close every sender and reader made from this object, then let go of the stream.

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


class End:
    """What a sender and a reader share: the native end, its stream, and closing it once."""

    __slots__ = ("__weakref__", "_lock", "_native", "_stream")

    def __init__(self, stream: SharedStream[Any], native: Sender | Reader) -> None:
        self._stream = stream
        self._native = native
        self._lock = threading.Lock()
        LIVE_ENDS.add(cast(Any, self))

    @property
    def closed(self) -> bool:
        return self._native.closed

    def close(self) -> None:
        """Close this end; a call waiting in it raises `StreamClosedError`. Calling it again does nothing."""
        self._native.close()


def steps(timeout: float | None) -> Iterator[float]:
    """Yield the length of each native wait for `timeout`: `STEP` without end for None, else until it passes."""
    if timeout is None:
        while True:
            yield STEP
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        yield max(0.0, min(STEP, left))
        if left <= STEP:
            return


class StreamSender(End, Generic[T]):
    """The sender of a stream: one per stream, in the process that holds it.

    [`SharedStream.sender`][sharedbox.SharedStream.sender] returns it.
    Closing it ends the stream: readers receive what is buffered, then
    [`EndOfStream`][sharedbox.EndOfStream].
    """

    __slots__ = ()
    _native: Sender

    def send(self, item: T, timeout: float | None = None) -> None:
        """Send `item`, waiting while a lossless reader is a full ring behind.

        Raises
        ------
        TimeoutError
            If `timeout` seconds pass first; the item is not sent.
        StreamClosedError
            If this sender is closed, before or during the wait.
        """
        self._send(item, timeout)

    def send_nowait(self, item: T) -> None:
        """Send `item`, or raise [`WouldBlock`][sharedbox.WouldBlock] if a lossless reader is a full ring behind."""
        self._native.send(item, 0.0)

    def _send(
        self, item: T, timeout: float | None, future: Future[Any] | None = None
    ) -> None:
        for step in steps(timeout):
            try:
                self._native.send(item, step)
                return
            except WouldBlock:
                continue
            except _Interrupted:
                if self.closed:
                    raise StreamClosedError(
                        f"stream {self._stream.name!r}: the sender was closed"
                    ) from None
                continue
        raise TimeoutError(
            f"stream {self._stream.name!r}: no lossless reader made room within {timeout} s"
        )

    def __enter__(self) -> StreamSender[T]:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __reduce__(self) -> Any:
        raise TypeError(
            "a StreamSender cannot be pickled; send the SharedStream and call sender() "
            "in the other process"
        )


class StreamReader(End, Iterator[T], Generic[T]):
    """A reader of a stream, in the process that opened it.

    [`SharedStream.reader`][sharedbox.SharedStream.reader] returns it.
    `for item in reader` receives until the stream ends.
    """

    __slots__ = ("_position",)
    _native: Reader

    def __init__(self, stream: SharedStream[Any], native: Reader) -> None:
        super().__init__(stream, native)
        self._position: int | None = None

    @property
    def mode(self) -> Mode:
        return MODE_NAMES[self._native.mode]

    @property
    def position(self) -> int | None:
        """The position of the last item received, None before the first."""
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
        """
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
        """
        return self._receive(self._targets(out), timeout)

    def receive_nowait(self) -> T:
        """Return the next item, or raise [`WouldBlock`][sharedbox.WouldBlock] if none is waiting."""
        return self._take([], 0.0)

    def receive_into_nowait(self, out: Any, /) -> T:
        """As [`receive_into`][sharedbox.StreamReader.receive_into], raising `WouldBlock` if no item is waiting."""
        return self._take(self._targets(out), 0.0)

    def iter_into(self, out: Any, /) -> IterInto[T]:
        """Return an iterator of items read into the arrays in `out`, as `receive_into` takes it, for `for` and `async for`."""
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

    def _take(self, targets: list[tuple[tuple[int, ...], Any]], step: float) -> T:
        item, position = self._native.receive(step, targets)
        self._position = position
        return cast(T, item)

    def _receive(
        self,
        targets: list[tuple[tuple[int, ...], Any]],
        timeout: float | None,
        future: Future[Any] | None = None,
    ) -> T:
        for step in steps(timeout):
            try:
                return self._take(targets, step)
            except WouldBlock:
                continue
            except _Interrupted:
                if self.closed:
                    raise StreamClosedError(
                        f"stream {self._stream.name!r}: the reader was closed"
                    ) from None
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


class IterInto(Iterator[T], Generic[T]):
    """Items of one reader read into the same arrays, for `for` loops."""

    __slots__ = ("_reader", "_targets")

    def __init__(
        self, reader: StreamReader[T], targets: list[tuple[tuple[int, ...], Any]]
    ) -> None:
        self._reader = reader
        self._targets = targets

    def __iter__(self) -> IterInto[T]:
        return self

    def __next__(self) -> T:
        try:
            return self._reader._receive(self._targets, None)
        except EndOfStream:
            raise StopIteration from None
