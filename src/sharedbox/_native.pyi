import sys
from collections.abc import Sequence
from typing import Any, Final, Never, Self, overload

from typing_extensions import disjoint_base

from ._layout import FieldSpec

if sys.version_info >= (3, 13):
    from types import CapsuleType
else:
    from typing_extensions import CapsuleType

LAYOUT_VERSION: Final[tuple[int, int]]
"""``(major, minor)`` of the segment layout this module reads and writes."""

class SegmentExistsError(FileExistsError):
    """A segment with that name already exists."""

class SegmentNotFoundError(FileNotFoundError):
    """No segment has that name."""

class SchemaMismatchError(TypeError):
    """The segment was created by a different class or layout."""

class BoxClosedError(ValueError):
    """The segment handle has been closed."""

class LockTimeoutError(TimeoutError):
    """The write lock stayed taken for longer than the lock timeout."""

class WaiterSlotsFullError(RuntimeError):
    """Every waiter slot of the box is taken."""

def check(kind: int, capacity: int, name: str, value: object) -> None:
    """Raise what writing ``value`` to a field of this kind and capacity would raise."""

def _process_start(pid: int) -> int:
    """The process's start time, 0 if no such process exists; for tests."""

def _process_alive(pid: int, start: int) -> bool:
    """True if a process with this pid and start time is running; for tests."""

@disjoint_base
class Segment:
    """A named shared-memory segment holding one fixed-layout record."""

    def __init__(self, *args: Never, **kwargs: Never) -> None:
        """No constructor: use :meth:`create` or :meth:`attach`."""

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
    ) -> Segment:
        """Create the segment with ``values`` written before any other process can see it.

        ``fields`` are ``(offset, capacity, kind)``; ``names`` are used in error messages.
        ``waiter_slots`` is how many threads, across processes, can wait at once.
        """

    @staticmethod
    def attach(
        name: str, names: Sequence[str], schema_hash: int, lock_timeout: float
    ) -> Segment:
        """Open an existing segment whose schema hash matches."""

    @staticmethod
    def unlink(name: str) -> None:
        """Remove the name, as ``shm_unlink`` does; a no-op on Windows."""

    def get(self, field: int) -> object:
        """The field's value."""

    def get_versioned(self, field: int) -> tuple[int, object]:
        """The field's version and value, read together."""

    def get_dict(self, names: tuple[str, ...]) -> dict[str, object]:
        """Every field's value under its name in ``names``, read at one point in time."""

    def set(self, values: Sequence[tuple[int, object]]) -> None:
        """Convert every value, then write them all under one lock."""

    def _read(self, field: int) -> bytes:
        """The field's bytes, read consistently with concurrent writes; for tests."""

    def _read_all(self) -> list[bytes]:
        """Every field's bytes, read at one point in time; for tests."""

    def _write(self, values: Sequence[tuple[int, bytes]]) -> None:
        """Write several fields' bytes under one lock; for tests."""

    def version(self, field: int) -> int:
        """How many writes the field has had."""

    def generation(self) -> int:
        """How many writes the segment has had."""

    def wait(
        self, last_generation: int, timeout: float, slot: int | None = None
    ) -> int:
        """Block until the generation differs from ``last_generation``, ``slot`` is interrupted, or ``timeout`` seconds pass.

        Returns the generation. Without ``slot`` the call claims a slot for its
        own duration. ``timeout`` must be finite and between 0 and 86400.
        """

    def register_waiter(self) -> int:
        """Claim a waiter slot for this process; free it with :meth:`release_waiter`."""

    def release_waiter(self, slot: int) -> None:
        """Free a slot claimed with :meth:`register_waiter`."""

    def waiter_held(self, slot: int) -> bool:
        """True while ``slot`` is still this process's; false once it was freed under it."""

    def interrupt(self, slot: int) -> None:
        """End the wait in ``slot``, in any process, or the next one if none is running."""

    def _export(self) -> CapsuleType:
        """A ``"sharedbox_box"`` capsule holding a handle with its own mapping of the segment."""

    def force_unlock(self) -> None:
        """Release a write lock left behind by a process that died while writing."""

    def _hold_write_lock(self) -> None:
        """Take the write lock and keep it until :meth:`_release_held_lock`; for tests."""

    def _release_held_lock(self) -> None:
        """Release the lock :meth:`_hold_write_lock` took, as its writer would; for tests."""

    def _after_fork(self) -> None:
        """Reset the handle's thread lock in a child created by ``fork``, before it starts threads."""

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
        """True after :meth:`close`."""

    @property
    def name(self) -> str: ...
    @property
    def lock_timeout(self) -> float:
        """Seconds a read or write waits for another writer's lock."""

@disjoint_base
class Field:
    """Reads and writes one field of the box it is accessed through."""

    def __init__(self, spec: FieldSpec, segment_slot: object) -> None:
        """``segment_slot`` is ``SharedBox.__dict__["_segment"]``, the descriptor of the slot holding a box's segment."""

    @property
    def spec(self) -> FieldSpec: ...
    @overload
    def __get__(self, box: None, owner: type | None = None, /) -> Self: ...
    @overload
    def __get__(self, box: object, owner: type | None = None, /) -> Any: ...
    def __set__(self, box: object, value: object, /) -> None: ...
