import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, Never, Self, final, overload

from typing_extensions import disjoint_base

from ._box import RefEntry
from ._layout import FieldSpec

if sys.version_info >= (3, 13):
    from types import CapsuleType
else:
    from typing_extensions import CapsuleType

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
    """Every waiter slot of the box is taken."""

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

        With `types`, the segment's description table must also equal
        `types.table`. A segment whose field count differs from the number
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
    ) -> None: ...
    @property
    def table(self) -> bytes:
        """The description table, as the segment stores it."""
    def check(self, field: int, value: object) -> None:
        """Raise what writing `value` to `field` would raise."""
    def decode(self, field: int, data: bytes) -> object:
        """Return the value stored as `data`, as `read_versioned` returns it."""

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
