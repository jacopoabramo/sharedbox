import sys
from collections.abc import (
    Callable,
    ItemsView,
    Iterable,
    Iterator,
    KeysView,
    Mapping,
    MutableMapping,
    MutableSequence,
    MutableSet,
    Sequence,
    ValuesView,
)
from collections.abc import (
    Set as AbstractSet,
)
from typing import (
    Any,
    ClassVar,
    Final,
    Generic,
    Never,
    Self,
    SupportsIndex,
    TypeVar,
    final,
    overload,
)

from _typeshed import SupportsKeysAndGetItem
from typing_extensions import disjoint_base

from ._box import RefEntry
from ._layout import FieldSpec

if sys.version_info >= (3, 13):
    from types import CapsuleType
else:
    from typing_extensions import CapsuleType

_T = TypeVar("_T")
_K = TypeVar("_K")
_V = TypeVar("_V")

LAYOUT_VERSION: Final[tuple[int, int]]
"""`(major, minor)` of the segment layout this module reads and writes."""

class SegmentExistsError(FileExistsError):
    """A segment with that name already exists.

    Raised by creating a box under a name that is taken. The message says
    which case it is:

    - the box's creator is still running, with its pid;
    - the box is still being created: its creator is running and has not
      returned from `__post_init__` yet. The message gives its pid and
      says to wait for it or use another name;
    - the creator runs in another pid namespace, such as another
      container;
    - the creator is no longer running. On Linux the box is then probably
      left over from a crash, and `unlink(name)` on its class removes it.
      On Windows a process, possibly this one, still has the box open, and
      the name is freed when every handle to it is closed;
    - the name holds no published box. On Linux it may be left over from a
      crash during creation, and `unlink(name)` on its class removes it. On
      Windows a process, possibly this one, still has something open under
      that name, and the name is freed when every handle to it is closed.

    When none of these can be told, the message only says the name is
    taken. Nothing is removed automatically.
    """

class SegmentNotFoundError(FileNotFoundError):
    """No segment has that name.

    Raised by attaching to, or unlinking on Linux, a name with no segment,
    and by attaching to shared memory that does not become a box within
    1 s.
    """

class SchemaMismatchError(TypeError):
    """The segment was created by a different class or layout.

    Raised by attaching, or unpickling a box, with a class whose identity
    or fields differ from the creator's; by a segment of another layout
    major version, with a field of a kind this version cannot read, or
    with an unknown magic (a segment made by another version of
    sharedbox, or not a sharedbox segment), which the message names; and by
    unpickling after the box was created again.
    """

class KindMismatchError(SchemaMismatchError):
    """The name holds a segment of another kind, such as a stream where a box was asked for.

    The message names both kinds.
    """

class BoxClosedError(ValueError):
    """The segment handle has been closed.

    Raised by using a box after [`close`][sharedbox.SharedBox.close].
    """

class LockTimeoutError(TimeoutError):
    """The write lock stayed taken for longer than the lock timeout.

    Raised by a read or write that waits for a write in progress for
    longer than the class's `lock_timeout`. The message names the process
    that holds the lock.
    """

class WaiterSlotsFullError(RuntimeError):
    """A stream has `max_readers` readers open, or has no free waiter slot."""

def check(kind: int, capacity: int, name: str, value: object) -> None:
    """Raise what writing `value` to a field of this kind and capacity would raise."""

def _process_start(pid: int) -> int:
    """Return the process's start time, 0 if no such process exists; for tests."""

def _process_alive(pid: int, start: int) -> bool:
    """Return True if a process with this pid and start time is running; for tests."""

@disjoint_base
class FieldWriter:
    """The write lock taken by [`Segment.begin_write`][sharedbox._native.Segment.begin_write]."""

    def end(self) -> None:
        """Count the write, release the lock and wake waiters; later calls do nothing."""

@disjoint_base
class Segment:
    """A named shared-memory segment holding one fixed-layout record."""

    def __init__(self, *args: Never, **kwargs: Never) -> None:
        """No constructor: use [`create`][sharedbox._native.Segment.create] or [`attach`][sharedbox._native.Segment.attach]."""

    @staticmethod
    def create(
        name: str,
        fields: Sequence[tuple[int, int, int]],
        names: Sequence[str],
        record_size: int,
        schema_hash: int,
        lock_timeout: float,
        values: Sequence[tuple[int, object]],
        waiter_slots: int = 64,
        publish: bool = True,
        types: Types | None = None,
    ) -> Segment:
        """Create the segment with `values` written before any other process can see it.

        Parameters
        ----------
        fields
            `(offset, capacity, kind)` of each field.
        names
            Used in error messages.
        waiter_slots
            How many threads, across processes, can wait at once.
        publish
            If false, no other process can attach until
            [`publish`][sharedbox._native.Segment.publish].
        types
            Converts the values of kinds above 5, and gives the description
            table the segment stores.
        """

    @staticmethod
    def attach(
        name: str,
        names: Sequence[str],
        schema_hash: int,
        lock_timeout: float,
        types: Types | None = None,
    ) -> Segment:
        """Open an existing segment whose schema hash matches.

        With `types`, the segment's description table must also equal the
        one `types` holds. A segment whose field count differs from the number
        of `names` raises `SchemaMismatchError`.
        """

    @staticmethod
    def unlink(name: str) -> None:
        """Remove the name, as `shm_unlink` does; a no-op on Windows."""

    def get(self, field: int, types: Types | None = None) -> object:
        """Return the field's value; `(create_id, schema_hash, name)`, or None when empty, for a reference field."""

    def begin_write(
        self, field: int, types: Types | None = None
    ) -> tuple[FieldWriter, object]:
        """Take the write lock and return it with an array viewing the array field in shared memory.

        Raises
        ------
        TypeError
            If the field is not an array.
        """

    def read_into(self, field: int, out: object, types: Types | None = None) -> None:
        """Copy an array field into `out`, a writable C-contiguous CPU array of the field's dtype and shape.

        Raises
        ------
        TypeError
            If the field is not an array, or `out` is not a writable
            C-contiguous array in CPU memory.
        ValueError
            If `out` has another dtype or shape.
        """

    def cached_ref(self, field: int, cache: dict[int, RefEntry]) -> object:
        """Return `cache[field][1]` if the reference field holds `cache[field][0]` and `cache[field][2]` is open.

        None when the field is empty, False otherwise. Decodes only the
        create id, not the name.

        Raises
        ------
        ValueError
            If the field is of another kind.
        TypeError
            If the entry is not `(create_id, box, Segment)`.
        """

    def get_versioned(
        self, field: int, types: Types | None = None
    ) -> tuple[int, object]:
        """Return the field's version and value, read together."""

    def read_versioned(self, field: int) -> tuple[int, bytes]:
        """Return the field's version and stored bytes, read together."""

    def get_dict(
        self, names: tuple[str, ...], types: Types | None = None
    ) -> dict[str, object]:
        """Return every field's value under its name in `names`, read at one point in time."""

    def set(
        self, values: Sequence[tuple[int, object]], types: Types | None = None
    ) -> None:
        """Convert every value, then write them all under one lock.

        A reference field takes `(create_id, schema_hash, name)`, or None to empty it.
        """

    def _read(self, field: int) -> bytes:
        """Return the field's bytes, read consistently with concurrent writes; for tests."""

    def _read_all(self) -> list[bytes]:
        """Return every field's bytes, read at one point in time; for tests."""

    def _write(self, values: Sequence[tuple[int, bytes]]) -> None:
        """Write several fields' bytes under one lock; for tests."""

    def version(self, field: int) -> int:
        """Return how many writes the field has had."""

    def versions(self) -> list[int]:
        """Return the [`version`][sharedbox._native.Segment.version] of every field, in field order."""

    def publish(self) -> None:
        """Let other processes attach to a segment created with `publish=False`.

        Raises
        ------
        ValueError
            If the segment is already published.
        """

    def generation(self) -> int:
        """Return how many writes the segment has had."""

    def wait(
        self, last_generation: int, timeout: float, slot: int | None = None
    ) -> int:
        """Block until the generation differs from `last_generation`, `slot` is interrupted, or `timeout` seconds pass.

        Returns the current generation.

        Parameters
        ----------
        slot
            If not given, the call claims a slot for its own duration.

        Raises
        ------
        ValueError
            If `timeout` is not finite or not between 0 and 86400.
        """

    def register_waiter(self) -> int:
        """Claim a waiter slot for this process; free it with [`release_waiter`][sharedbox._native.Segment.release_waiter]."""

    def release_waiter(self, slot: int) -> None:
        """Free a slot claimed with [`register_waiter`][sharedbox._native.Segment.register_waiter]."""

    def waiter_held(self, slot: int) -> bool:
        """Return True while `slot` is still this process's, and False once it was freed under it."""

    def interrupt(self, slot: int) -> None:
        """End the wait in `slot`, in any process, or the next one if none is running."""

    def _export(self) -> CapsuleType:
        """Return a `"sharedbox_box"` capsule holding a handle with its own mapping of the segment."""

    def force_unlock(self) -> None:
        """Release a write lock left behind by a process that died while writing."""

    def _hold_write_lock(self) -> None:
        """Take the write lock and keep it until [`_release_held_lock`][sharedbox._native.Segment._release_held_lock]; for tests."""

    def _release_held_lock(self) -> None:
        """Release the lock [`_hold_write_lock`][sharedbox._native.Segment._hold_write_lock] took, as its writer would; for tests."""

    def _after_fork(self) -> None:
        """Reset the handle's thread lock in a child created by `fork`, before it starts threads."""

    def close(self) -> None:
        """Detach this handle; the segment stays until unlinked."""

    @property
    def _size(self) -> int:
        """Bytes of the segment's mapping."""

    @property
    def _waiters(self) -> int:
        """Occupied waiter slots; for tests."""

    @property
    def create_id(self) -> int:
        """Random at creation; a box made again under the same name has another."""

    @property
    def closed(self) -> bool:
        """True after [`close`][sharedbox._native.Segment.close]."""

    @property
    def published(self) -> bool:
        """False between `create(..., publish=False)` and [`publish`][sharedbox._native.Segment.publish]."""

    @property
    def name(self) -> str: ...
    @property
    def lock_timeout(self) -> float:
        """Seconds a read or write waits for another writer's lock."""

    @property
    def layout_version(self) -> tuple[int, int]:
        """`(major, minor)` of the segment's layout."""

@disjoint_base
class Types:
    """The field types of one class: its description table and what converting values needs."""

    def __init__(
        self,
        fields: Sequence[tuple[int, int, int]],
        labels: Sequence[str],
        table: bytes,
        info: Mapping[int, tuple[object, ...]],
        bytearrays: Sequence[tuple[int, int]],
        reusable: Sequence[int] = (),
        converts: Sequence[
            tuple[int, int, Callable[[Any], Any] | None, Callable[[Any], Any] | None]
        ] = (),
    ) -> None: ...
    def check(self, field: int, value: object) -> None:
        """Raise what writing `value` to `field` would raise."""
    def decode(self, field: int, data: bytes) -> object:
        """Return the value stored as `data`, as `read_versioned` returns it."""

@disjoint_base
class NativeCollection(MutableSequence[_T]):
    """A list value held as its field stores it: the count and the slots it uses.

    A read of a list field makes one, and so do a slice, a copy, `+` and
    `*`; Python code cannot construct it.
    """

    __hash__: ClassVar[None]  # type: ignore[assignment]
    def __len__(self) -> int: ...
    @overload
    def __getitem__(self, index: SupportsIndex, /) -> _T:
        """Decode one element, counted from the end when negative.

        Raises
        ------
        IndexError
            If the value has no such element.
        """
    @overload
    def __getitem__(self, index: slice, /) -> Self:
        """Return a new value of the same element type and capacity holding a slice.

        The slots are copied without decoding an element.
        """
    @overload
    def __setitem__(self, index: SupportsIndex, value: _T, /) -> None:
        """Replace one element after checking it as a write to the field would.

        Raises
        ------
        IndexError
            If the value has no such element.
        TypeError
            If the element is not of the field's element type.
        ValueError
            If the element is of that type but does not fit, such as a str
            longer than its capacity.
        """
    @overload
    def __setitem__(self, index: slice, value: Iterable[_T], /) -> None:
        """Replace the elements of a slice with those of an iterable, all of them or none.

        A slice of step 1 may change the length; one of another step takes
        as many elements as it covers, as for `list`.

        Raises
        ------
        TypeError
            If an element is not of the field's element type.
        ValueError
            If an element does not fit, the value would hold more elements
            than its capacity, or a slice of a step other than 1 covers a
            different number of elements.
        """
    def __delitem__(self, index: SupportsIndex | slice, /) -> None:
        """Remove one element, or the elements of a slice.

        Raises
        ------
        IndexError
            If the value has no such element.
        """
    def insert(self, index: SupportsIndex, value: _T, /) -> None:
        """Insert an element before `index`, clamped to the value as `list.insert` clamps it.

        Raises
        ------
        OverflowError
            If `index` does not fit a C `Py_ssize_t`.
        TypeError
            If the element is not of the field's element type.
        ValueError
            If the element does not fit, or the value already holds its
            capacity of elements.
        """
    def append(self, value: _T, /) -> None:
        """Add an element at the end.

        Raises
        ------
        TypeError
            If the element is not of the field's element type.
        ValueError
            If the element does not fit, or the value already holds its
            capacity of elements.
        """
    def extend(self, values: Iterable[_T], /) -> None:
        """Append the elements of an iterable, all of them or none.

        Raises
        ------
        TypeError
            If an element is not of the field's element type.
        ValueError
            If an element does not fit, or the value would hold more
            elements than its capacity.
        """
    def __iadd__(self, values: Iterable[_T], /) -> Self:
        """Extend with the elements of an iterable, as `extend` does.

        Raises
        ------
        TypeError
            If an element is not of the field's element type.
        ValueError
            If an element does not fit, or the value would hold more
            elements than its capacity.
        """
    def pop(self, index: SupportsIndex = -1, /) -> _T:
        """Remove the element at `index`, the last by default, and return it.

        An element that fails to decode stays in the value.

        Raises
        ------
        OverflowError
            If `index` does not fit a C `Py_ssize_t`.
        IndexError
            If the value is empty or has no such element.
        """
    def remove(self, value: _T, /) -> None:
        """Remove the first element equal to `value`.

        Raises
        ------
        ValueError
            If no element is equal to `value`.
        """
    def index(
        self, value: _T, start: SupportsIndex = 0, stop: SupportsIndex = sys.maxsize, /
    ) -> int:
        """Return the position of the first element equal to `value` from `start` to `stop`.

        Raises
        ------
        ValueError
            If no element in that range is equal to `value`.
        """
    def count(self, value: _T, /) -> int:
        """Return the number of elements equal to `value`."""
    def __contains__(self, value: object, /) -> bool: ...
    def reverse(self) -> None:
        """Reverse the elements in place without decoding them."""
    def clear(self) -> None:
        """Remove every element."""
    def __eq__(self, other: object, /) -> bool:
        """Compare the elements in order with those of a list or another value."""
    def __reversed__(self) -> Iterator[_T]: ...
    def __iter__(self) -> Iterator[_T]: ...
    @property
    def _element_annotation(self) -> object:
        """The annotation of the elements, as the field declares it."""
    @property
    def capacity(self) -> int:
        """The most elements the value can hold."""
    def copy(self) -> Self:
        """Return a new value of the same element type and capacity holding the same elements.

        The slots are copied without decoding an element.
        """
    def __add__(self, other: list[_T] | NativeCollection[_T], /) -> Self:
        """Return a copy extended with the elements of `other`.

        Raises
        ------
        TypeError
            If an element is not of the field's element type.
        ValueError
            If an element does not fit, or the result would hold more
            elements than the capacity.
        """
    def __mul__(self, times: SupportsIndex, /) -> Self:
        """Return a new value holding the elements `times` times over, none below 1.

        Raises
        ------
        OverflowError
            If `times` does not fit a C `Py_ssize_t`.
        ValueError
            If the result would hold more elements than the capacity.
        """
    def __rmul__(self, times: SupportsIndex, /) -> Self:
        """Return the elements `times` times over, as `*` does."""
    def __imul__(self, times: SupportsIndex, /) -> Self:
        """Repeat the elements in place `times` times over, removing them below 1.

        Raises
        ------
        OverflowError
            If `times` does not fit a C `Py_ssize_t`.
        ValueError
            If the value would hold more elements than the capacity; it is
            left as it was.
        """
    def _stored(self) -> bytes:
        """Return the count and used slots, as a list field stores them."""

@final
class NativeCollectionIterator(Generic[_T]):
    """Decodes one element of a `NativeCollection` per step, forward or from the end."""

    def __iter__(self) -> Self: ...
    def __next__(self) -> _T: ...

@disjoint_base
class NativeSet(MutableSet[_T]):
    """A set value held as its field stores it: the count and the slots it uses.

    A read of a set field declared with a `NativeSet` subclass makes one,
    and so does a copy; Python code cannot construct it.

    Keys are found by their stored bytes. A value that cannot be encoded
    as a key, such as one of another type, is never held.
    """

    __hash__: ClassVar[None]  # type: ignore[assignment]
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[_T]:
        """Decode one element per step, in the order the slots hold them.

        Raises
        ------
        RuntimeError
            If the value's length changed since the iteration began.
        """
    def __contains__(self, value: object, /) -> bool: ...
    def add(self, value: _T, /) -> None:
        """Add `value` unless the set holds it.

        Raises
        ------
        TypeError
            If `value` is not of the field's element type.
        ValueError
            If `value` does not fit, is a key the field refuses (a nan, a
            NaT, an aware `datetime` or `time`), or the set is full.
        """
    def discard(self, value: _T, /) -> None:
        """Remove `value` if the set holds it."""
    def remove(self, value: _T, /) -> None:
        """Remove `value`.

        Raises
        ------
        KeyError
            If the set does not hold `value`.
        """
    def pop(self) -> _T:
        """Remove the element in the last slot and return it.

        An element that fails to decode stays in the value.

        Raises
        ------
        KeyError
            If the value is empty.
        """
    def clear(self) -> None:
        """Remove every element."""
    def update(self, *iterables: Iterable[_T]) -> None:
        """Add the elements of every iterable, all of them or none.

        Raises
        ------
        TypeError
            If an element is not of the field's element type, or an
            iterable is a value that was never built.
        ValueError
            As `add` raises it, or if the distinct new elements would pass
            the capacity.
        """
    def __eq__(self, other: object, /) -> bool:
        """Compare with a set: equal when both hold the same keys, in any order.

        Raises
        ------
        TypeError
            If `other` is a value that was never built.
        """
    def __or__(self, other: AbstractSet[_T], /) -> Self:  # type: ignore[override]
        """Return a new value of the same class and capacity holding the elements of both sets.

        Raises
        ------
        TypeError
            If an element of `other` is not of the field's element type,
            or `other` is a value that was never built.
        ValueError
            As `add` raises it, or if the result would hold more elements
            than the capacity.
        """
    def __ror__(self, other: AbstractSet[_T], /) -> Self:
        """Return the elements of both sets, as `|` does."""
    def __and__(self, other: AbstractSet[object], /) -> Self:
        """Return a new value of the same class and capacity holding the elements both sets hold.

        Raises
        ------
        TypeError
            If `other` is a value that was never built.
        """
    def __rand__(self, other: AbstractSet[object], /) -> Self:
        """Return the elements both sets hold, as `&` does."""
    def __sub__(self, other: AbstractSet[object], /) -> Self:
        """Return a new value of the same class and capacity holding the elements of this set that `other` does not hold.

        Raises
        ------
        TypeError
            If `other` is a value that was never built.
        """
    def __rsub__(self, other: AbstractSet[_T], /) -> Self:
        """Return a new value of the same class and capacity holding the elements of `other` this set does not hold.

        Raises
        ------
        TypeError
            If an element of `other` is not of the field's element type,
            or `other` is a value that was never built.
        ValueError
            As `add` raises it, or if the result would hold more elements
            than the capacity.
        """
    def __xor__(self, other: AbstractSet[_T], /) -> Self:  # type: ignore[override]
        """Return a new value of the same class and capacity holding the elements only one set holds.

        Raises
        ------
        TypeError
            If an element of `other` is not of the field's element type,
            or `other` is a value that was never built.
        ValueError
            As `add` raises it, or if the result would hold more elements
            than the capacity.
        """
    def __rxor__(self, other: AbstractSet[_T], /) -> Self:
        """Return the elements only one set holds, as `^` does."""
    def __ior__(self, other: AbstractSet[_T], /) -> Self:  # type: ignore[override]
        """Add the elements of `other`, all of them or none, as `update` does.

        Raises
        ------
        TypeError
            If an element of `other` is not of the field's element type,
            or `other` is a value that was never built.
        ValueError
            As `add` raises it, or if the set would hold more elements than
            the capacity; it is left as it was.
        """
    def __iand__(self, other: AbstractSet[object], /) -> Self:
        """Keep only the elements `other` holds.

        Raises
        ------
        TypeError
            If `other` is a value that was never built.
        """
    def __isub__(self, other: AbstractSet[object], /) -> Self:
        """Remove the elements `other` holds.

        Raises
        ------
        TypeError
            If `other` is a value that was never built.
        """
    def __ixor__(self, other: AbstractSet[_T], /) -> Self:  # type: ignore[override]
        """Remove the elements `other` holds and add those it alone holds, all or nothing.

        Raises
        ------
        TypeError
            If an element of `other` is not of the field's element type,
            or `other` is a value that was never built.
        ValueError
            As `add` raises it, or if the set would hold more elements than
            the capacity; it is left as it was.
        """
    def __le__(self, other: AbstractSet[object], /) -> bool:
        """Return whether `other` holds every element of this set.

        Raises
        ------
        TypeError
            If `other` is a value that was never built.
        """
    def __lt__(self, other: AbstractSet[object], /) -> bool:
        """Return whether `other` holds every element of this set and at least one more.

        Raises
        ------
        TypeError
            If `other` is a value that was never built.
        """
    def __ge__(self, other: AbstractSet[object], /) -> bool:
        """Return whether this set holds every element of `other`.

        Raises
        ------
        TypeError
            If `other` is a value that was never built.
        """
    def __gt__(self, other: AbstractSet[object], /) -> bool:
        """Return whether this set holds every element of `other` and at least one more.

        Raises
        ------
        TypeError
            If `other` is a value that was never built.
        """
    def isdisjoint(self, other: Iterable[object], /) -> bool:
        """Return whether this set holds none of the elements of `other`.

        Raises
        ------
        TypeError
            If `other` is a value that was never built.
        """
    @property
    def _element_annotation(self) -> object:
        """The annotation of the elements, as the field declares it."""
    @property
    def capacity(self) -> int:
        """The most elements the value can hold."""
    def copy(self) -> Self:
        """Return a new value of the same element type and capacity holding the same elements.

        The slots are copied without decoding an element.
        """
    def _stored(self) -> bytes:
        """Return the count and used slots, as a set field stores them."""

@final
class NativeSetIterator(Generic[_T]):
    """Decodes one element of a `NativeSet` per step, in slot order."""

    def __iter__(self) -> Self: ...
    def __next__(self) -> _T: ...

@disjoint_base
class NativeDict(MutableMapping[_K, _V]):
    """A dict value held as its field stores it: the count and the slots it uses, each a key and its value.

    A read of a dict field declared with a `NativeDict` subclass makes one,
    and so does a copy; Python code cannot construct it.

    Keys are found by their stored bytes. A key that cannot be encoded,
    such as one of another type, is never held.
    """

    __hash__: ClassVar[None]  # type: ignore[assignment]
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[_K]:
        """Decode one key per step, in insertion order.

        Raises
        ------
        RuntimeError
            If the value's length changed since the iteration began.
        """
    def __reversed__(self) -> Iterator[_K]:
        """Decode one key per step, from the last inserted to the first.

        Raises
        ------
        RuntimeError
            If the value's length changed since the iteration began.
        """
    def __getitem__(self, key: _K, /) -> _V:
        """Return the value of `key`.

        Raises
        ------
        KeyError
            If the dict holds no such key.
        """
    @overload
    def get(self, key: _K, default: None = None, /) -> _V | None: ...
    @overload
    def get(self, key: _K, default: _V | _T, /) -> _V | _T: ...
    def __contains__(self, key: object, /) -> bool: ...
    def __eq__(self, other: object, /) -> bool:
        """Compare with a mapping: equal when both hold the same keys with equal values, in any order.

        Raises
        ------
        TypeError
            If `other` is a value that was never built.
        """
    def __setitem__(self, key: _K, value: _V, /) -> None:
        """Set the value of `key`, in its place when the dict holds it, or after the last item.

        Raises
        ------
        TypeError
            If `key` or `value` is not of the field's type.
        ValueError
            If `key` or `value` does not fit, `key` is one the field
            refuses (a nan, a NaT, an aware `datetime` or `time`), or `key`
            is new and the dict is full.
        """
    def __delitem__(self, key: _K, /) -> None:
        """Remove `key` and its value, keeping the order of the other items.

        Raises
        ------
        KeyError
            If the dict holds no such key.
        """
    @overload
    def pop(self, key: _K, /) -> _V:
        """Remove `key` and return its value.

        A value that fails to decode stays in the dict.

        Raises
        ------
        KeyError
            If the dict holds no such key.
        """
    @overload
    def pop(self, key: _K, default: _V, /) -> _V: ...
    @overload
    def pop(self, key: _K, default: _T, /) -> _V | _T:
        """Remove `key` and return its value, or return `default` when the dict holds no such key."""
    def popitem(self) -> tuple[_K, _V]:
        """Remove the last item and return it.

        An item that fails to decode stays in the value.

        Raises
        ------
        KeyError
            If the value is empty.
        """
    @overload
    def setdefault(
        self: NativeDict[_K, _T | None], key: _K, default: None = None, /
    ) -> _T | None: ...
    @overload
    def setdefault(self, key: _K, default: _V, /) -> _V:
        """Return the value of `key`, or set it to `default` and return `default` when the dict holds no such key.

        `default` is checked only when it is set.

        Raises
        ------
        TypeError
            If `key`, or `default` when it is set, is not of the field's type.
        ValueError
            As `__setitem__` raises it.
        """
    @overload
    def update(self, other: SupportsKeysAndGetItem[_K, _V], /) -> None: ...
    @overload
    def update(
        self: NativeDict[str, _V],
        other: SupportsKeysAndGetItem[str, _V],
        /,
        **kwargs: _V,
    ) -> None: ...
    @overload
    def update(self, other: Iterable[tuple[_K, _V]], /) -> None: ...
    @overload
    def update(
        self: NativeDict[str, _V], other: Iterable[tuple[str, _V]], /, **kwargs: _V
    ) -> None: ...
    @overload
    def update(self: NativeDict[str, _V], **kwargs: _V) -> None:
        """Set the items of `other`, then those of `kwargs`, all of them or none.

        `other` is read as a mapping when it has a `keys` method, and
        otherwise as pairs.

        Raises
        ------
        TypeError
            If a key or value is not of the field's type, an element of
            `other` is not a pair, or `other` is a value that was never
            built.
        ValueError
            As `__setitem__` raises it, if an element of `other` is not two
            long, or if the new keys would pass the capacity.
        """
    def clear(self) -> None:
        """Remove every item."""
    def __or__(self, other: Mapping[_K, _V], /) -> Self:
        """Return a new value of the same class and capacity holding these items, then those of `other`.

        Raises
        ------
        TypeError
            If a key or value of `other` is not of the field's type, or
            `other` is a value that was never built.
        ValueError
            As `__setitem__` raises it, or if the result would hold more
            keys than the capacity.
        """
    def __ror__(self, other: Mapping[_K, _V], /) -> Self:
        """Return a new value of the same class and capacity holding the items of `other`, then these, as `|` does."""
    def __ior__(self, other: Mapping[_K, _V] | Iterable[tuple[_K, _V]], /) -> Self:
        """Set the items of `other`, all of them or none, as `update` does.

        Raises
        ------
        TypeError
            If a key or value of `other` is not of the field's type, or
            `other` is a value that was never built.
        ValueError
            As `__setitem__` raises it, or if the dict would hold more keys
            than the capacity; it is left as it was.
        """
    def keys(self) -> KeysView[_K]: ...
    def values(self) -> ValuesView[_V]: ...
    def items(self) -> ItemsView[_K, _V]: ...
    @property
    def _element_annotation(self) -> object:
        """The annotations of the key and the value, as a pair, as the field declares them."""
    @property
    def capacity(self) -> int:
        """The most items the value can hold."""
    def copy(self) -> Self:
        """Return a new value of the same types and capacity holding the same items.

        The slots are copied without decoding a key or value.
        """
    def _stored(self) -> bytes:
        """Return the count and used slots, as a dict field stores them."""

@final
class NativeDictIterator(Generic[_T]):
    """Decodes one key of a `NativeDict` per step, in insertion order or reversed."""

    def __iter__(self) -> Self: ...
    def __next__(self) -> _T: ...

@disjoint_base
class Field:
    """Reads and writes one field of the box it is accessed through."""

    def __init__(
        self,
        spec: FieldSpec,
        segment_slot: object,
        types: Types | None = None,
        values_slot: object | None = None,
    ) -> None:
        """Read and write the field `spec` describes.

        Parameters
        ----------
        segment_slot
            `SharedBox.__dict__["_segment"]`, the descriptor of the slot
            holding a box's segment.
        types
            The class's types, which a field of a kind above 5 needs.
        values_slot
            `SharedBox.__dict__["_values"]`, the descriptor of the slot
            holding a box's [`ValueCache`][sharedbox._native.ValueCache];
            without it, every read decodes a new value.
        """

    @property
    def spec(self) -> FieldSpec: ...
    @overload
    def __get__(self, box: None, owner: type | None = None, /) -> Self: ...
    @overload
    def __get__(self, box: object, owner: type | None = None, /) -> Any: ...
    def __set__(self, box: object, value: object, /) -> None: ...

@disjoint_base
class ValueCache:
    """The values reads of one box decoded from fields whose values cannot be changed.

    Each is kept with the field's write count at the time, and a read
    returns it again while the count has not moved.
    """

    def __init__(self, fields: int) -> None: ...
    def clear(self) -> None:
        """Drop every value."""
    def _after_fork(self) -> None:
        """Make the locks a forked child inherited usable; call before another thread starts."""

@final
class BoxMethod:
    """A native `update` or `snapshot`, bound to the box it is called on."""

    def __new__(
        cls,
        kind: int,
        owner: type,
        qualname: str,
        names: tuple[str, ...],
        specs: tuple[FieldSpec | None, ...],
        helper: Callable[..., object],
        follow: Callable[..., object] | None,
        fallback: Callable[..., object],
        segment_slot: object,
        types: Types | None = None,
    ) -> Self: ...
    @property
    def __name__(self) -> str: ...
    @property
    def __qualname__(self) -> str: ...
    @property
    def __doc__(self) -> str | None: ...  # type: ignore[override]
    @property
    def __wrapped__(self) -> Callable[..., object]: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...
    def __get__(self, obj: object, objtype: type | None = None, /) -> Any: ...
    def __reduce__(self) -> tuple[Callable[..., Any], tuple[type, str]]: ...

class WouldBlock(Exception):
    """A non-blocking stream call that would have had to wait."""

class EndOfStream(Exception):
    """The stream's sender closed or died and every item it sent was received."""

class StreamBusyError(RuntimeError):
    """Another live end holds the stream's sender."""

class StreamClosedError(ValueError):
    """A stream or one of its ends used after `close()`, or in a child of `fork`."""

class _Interrupted(Exception): ...

@disjoint_base
class Sender:
    def send(self, value: object, timeout: float) -> int: ...
    def reserve(self, timeout: float) -> None: ...
    def commit(self, value: object) -> int: ...
    def abandon(self) -> None: ...
    def interrupt(self) -> None: ...
    def close(self) -> None: ...
    def _after_fork(self) -> None: ...
    @property
    def closed(self) -> bool: ...

@disjoint_base
class Slot(Generic[_T]):
    def __init__(self, sender: Sender, template: _T | None) -> None: ...
    @property
    def item(self) -> _T:
        """The item to fill: the template's members, with arrays over the slot.

        Each array the item reaches through records and tuples is a writable
        array of its member's declared type, dtype and shape, which views
        the slot in shared memory and starts with the bytes of an older
        item. The arrays keep the stream's mapping alive, so using them
        after the block or after the stream is closed cannot crash, but
        writing to them after [`send`][sharedbox.SendSlot.send] changes
        an item readers may have received and tells no one.
        """
    def send(self, item: _T) -> None:
        """Encode `item` into the slot and publish it.

        An array of `item` that already is its place in the slot, such as
        one of [`item`][sharedbox.SendSlot.item]'s arrays, is not copied;
        any other array is copied into the slot.

        Raises
        ------
        RuntimeError
            If this slot was sent or given up already.
        StreamClosedError
            If the sender was closed after the slot was reserved; nothing is
            published.
        TypeError
            If `item` does not have the stream's item type; the slot is
            given up.
        ValueError
            If a value in `item` does not fit; the slot is given up.
        """
    def abandon(self) -> None:
        """Give the slot up unless it was sent: nothing is published, and the next send takes its position.

        Leaving the block of [`writing`][sharedbox.SharedSendStream.writing]
        does this.
        """

@disjoint_base
class Reader:
    def receive(
        self, timeout: float, arrays: Sequence[tuple[Sequence[int], object]] = ()
    ) -> tuple[Any, int]: ...
    @property
    def mode(self) -> int: ...
    @property
    def missed(self) -> int: ...
    def interrupt(self) -> None: ...
    def close(self) -> None: ...
    def _after_fork(self) -> None: ...
    @property
    def closed(self) -> bool: ...

@disjoint_base
class Stream:
    @staticmethod
    def create(
        name: str,
        types: Types,
        entry: int,
        schema_hash: int,
        capacity: int,
        max_readers: int,
    ) -> Stream: ...
    @staticmethod
    def attach(name: str, types: Types, entry: int, schema_hash: int) -> Stream: ...
    @property
    def name(self) -> str: ...
    @property
    def capacity(self) -> int: ...
    @property
    def max_readers(self) -> int: ...
    @property
    def create_id(self) -> int: ...
    @property
    def closed(self) -> bool: ...
    def statistics(self) -> tuple[int, bool, int, list[tuple[int, int, int]]]: ...
    def sender(self) -> Sender: ...
    def reader(self, mode: int, newest: bool) -> Reader: ...
    def close(self) -> None: ...
