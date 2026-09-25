from collections.abc import Sequence
from typing import Never

from typing_extensions import disjoint_base

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

@disjoint_base
class Segment:
    """A named shared-memory segment holding one fixed-layout record."""

    def __init__(self, *args: Never, **kwargs: Never) -> None:
        """No constructor: use :meth:`create` or :meth:`attach`."""

    @staticmethod
    def create(
        name: str,
        fields: Sequence[tuple[int, int, bool]],
        record_size: int,
        schema_hash: int,
        lock_timeout: float,
    ) -> Segment:
        """Create the segment; ``fields`` are ``(offset, capacity, prefixed)``."""

    @staticmethod
    def attach(name: str, schema_hash: int, lock_timeout: float) -> Segment:
        """Open an existing segment whose schema hash matches."""

    @staticmethod
    def unlink(name: str) -> None:
        """Remove the name, as ``shm_unlink`` does; a no-op on Windows."""

    def read(self, field: int) -> bytes:
        """The field's bytes, read consistently with concurrent writes."""

    def read_all(self) -> list[bytes]:
        """Every field's bytes, read at one point in time."""

    def write(self, values: Sequence[tuple[int, bytes]]) -> None:
        """Write several fields under one lock."""

    def version(self, field: int) -> int:
        """How many writes the field has had."""

    def generation(self) -> int:
        """How many writes the segment has had."""

    def wait(self, last_generation: int, timeout: float) -> int:
        """Block until the generation differs from ``last_generation`` or ``timeout`` seconds pass.

        ``timeout`` must be finite and between 0 and 86400.
        """

    def force_unlock(self) -> None:
        """Release a write lock left behind by a process that died while writing."""

    def _hold_write_lock(self) -> None:
        """Take the write lock and never release it; for tests."""

    def _after_fork(self) -> None:
        """Reset the handle's thread lock in a child created by ``fork``, before it starts threads."""

    def close(self) -> None:
        """Detach this handle; the segment stays until unlinked."""

    @property
    def closed(self) -> bool:
        """True after :meth:`close`."""

    @property
    def name(self) -> str: ...
