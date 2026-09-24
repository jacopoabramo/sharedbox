from collections.abc import Sequence
import enum


class SegmentExistsError(FileExistsError):
    pass

class SegmentNotFoundError(FileNotFoundError):
    pass

class SchemaMismatchError(TypeError):
    pass

class BoxClosedError(ValueError):
    pass

class LockTimeoutError(TimeoutError):
    pass

class FieldKind(enum.Enum):
    FIXED = 0

    PREFIXED = 1

class FieldDesc:
    def __init__(self, offset: int, capacity: int, kind: FieldKind) -> None: ...

    @property
    def offset(self) -> int: ...

    @property
    def capacity(self) -> int: ...

    @property
    def kind(self) -> FieldKind: ...

class Segment:
    @staticmethod
    def create(name: str, fields: Sequence[FieldDesc], record_size: int, schema_hash: int, lock_timeout: float) -> Segment: ...

    @staticmethod
    def attach(name: str, schema_hash: int, lock_timeout: float) -> Segment: ...

    def read(self, field: int) -> bytes: ...

    def read_all(self) -> list[bytes]: ...

    def write(self, values: Sequence[tuple[int, bytes]]) -> None: ...

    def version(self, field: int) -> int: ...

    def generation(self) -> int: ...

    def wait(self, last_generation: int, timeout: float) -> int: ...

    def force_unlock(self) -> None: ...

    def close(self) -> None: ...

    @staticmethod
    def unlink(name: str) -> None: ...

    @property
    def closed(self) -> bool: ...

    @property
    def name(self) -> str: ...
