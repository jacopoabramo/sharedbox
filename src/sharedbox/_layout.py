from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping
from dataclasses import KW_ONLY, dataclass
from typing import (
    Annotated,
    Any,
    ClassVar,
    Final,
    Literal,
    NamedTuple,
    get_args,
    get_origin,
    get_type_hints,
)

Kind = Literal["bool", "int", "float", "str", "bytes"]

MAX_CAPACITY: Final[int] = 1 << 20
MAX_FIELDS: Final[int] = 256
ALIGN: Final[int] = 8
INT: Final[struct.Struct] = struct.Struct("<q")
FLOAT: Final[struct.Struct] = struct.Struct("<d")
SCALARS: Final[dict[type, tuple[Kind, int]]] = {bool: ("bool", 1), int: ("int", 8), float: ("float", 8)}
PREFIXED: Final[tuple[Kind, ...]] = ("str", "bytes")


@dataclass(frozen=True)
class Capacity:
    """Maximum encoded size of a ``str`` or ``bytes`` field."""

    size: int
    """Bytes, not characters: UTF-8 text can need more than one per character."""

    def __post_init__(self) -> None:
        if not 0 < self.size <= MAX_CAPACITY:
            raise ValueError(f"capacity must be between 1 and {MAX_CAPACITY} bytes, got {self.size}")


class NativeField(NamedTuple):
    """A field's place in the record, in the form the native segment takes."""

    offset: int
    """Byte offset of the field from the start of the record."""
    capacity: int
    """Bytes reserved for the value, not counting the length prefix."""
    prefixed: bool
    """True for ``str`` and ``bytes``: a 4-byte length precedes the data."""


@dataclass(frozen=True)
class FieldSpec:
    """Place and encoding of one field in a shared record."""

    name: str
    index: int
    """Position in declaration order; also the field's number in the native segment."""
    kind: Kind
    """One of ``"bool"``, ``"int"``, ``"float"``, ``"str"``, ``"bytes"``."""
    offset: int
    """Byte offset of the field from the start of the record."""
    capacity: int
    """Encoded size in bytes; for ``str`` and ``bytes`` the most the value may take."""
    kw_only: bool = False
    """True after a ``KW_ONLY`` annotation or in a ``kw_only=True`` class."""

    @property
    def native(self) -> NativeField:
        return NativeField(self.offset, self.capacity, self.kind in PREFIXED)

    def encode(self, value: Any) -> bytes:
        """Encode ``value`` for this field; raises before anything is written."""
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise self._type_error("bool", value)
            return b"\x01" if value else b"\x00"
        if self.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise self._type_error("int", value)
            try:
                return INT.pack(value)
            except struct.error:
                raise OverflowError(f"{self.name} holds a signed 64-bit integer; {value} does not fit") from None
        if self.kind == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise self._type_error("float", value)
            return FLOAT.pack(float(value))
        if self.kind == "str":
            if not isinstance(value, str):
                raise self._type_error("str", value)
            data = value.encode("utf-8")
        else:
            if not isinstance(value, (bytes, bytearray, memoryview)):
                raise self._type_error("bytes", value)
            data = bytes(value)
        if len(data) > self.capacity:
            raise ValueError(f"{self.name} holds at most {self.capacity} bytes; the value encodes to {len(data)}")
        return data

    def decode(self, raw: bytes) -> Any:
        """Decode bytes read from this field."""
        if self.kind == "bool":
            return raw != b"\x00"
        if self.kind == "int":
            return INT.unpack(raw)[0]
        if self.kind == "float":
            return FLOAT.unpack(raw)[0]
        if self.kind == "str":
            return raw.decode("utf-8", errors="replace")
        return raw

    def _type_error(self, expected: str, value: object) -> TypeError:
        return TypeError(f"{self.name} expects {expected}, got {type(value).__name__}")


@dataclass(frozen=True)
class Layout:
    """Every field of a class, in declaration order, and the record they fill."""

    fields: tuple[FieldSpec, ...]
    """In declaration order, base classes first."""
    record_size: int
    """Bytes the record needs, a multiple of 8."""
    schema_hash: int
    """First 8 bytes of SHA-256 over the class identity and every field's name, kind and capacity."""
    by_name: Mapping[str, FieldSpec]


def classify(name: str, hint: object) -> tuple[Kind, int]:
    if isinstance(hint, type) and hint in SCALARS:
        return SCALARS[hint]
    if get_origin(hint) is Annotated:
        base, *extras = get_args(hint)
        capacities = [extra for extra in extras if isinstance(extra, Capacity)]
        if base in (str, bytes) and len(capacities) == 1:
            return base.__name__, capacities[0].size
    raise TypeError(
        f"field {name!r}: unsupported annotation {hint!r}; use bool, int, float, "
        "Annotated[str, Capacity(n)] or Annotated[bytes, Capacity(n)]"
    )


def class_identity(cls: type) -> str:
    """``module.qualname`` of ``cls``, the same in a spawned child as in its parent."""
    # multiprocessing's spawn start method re-imports the main script as __mp_main__.
    module = "__main__" if cls.__module__ == "__mp_main__" else cls.__module__
    return f"{module}.{cls.__qualname__}"


def build_layout(cls: type, kw_only: bool = False) -> Layout:
    """Lay out the public annotated fields of ``cls``, base classes first.

    Fields after a ``dataclasses.KW_ONLY`` annotation, or every field when
    ``kw_only`` is true, are keyword-only.
    """
    specs: list[FieldSpec] = []
    offset = 0
    for name, hint in get_type_hints(cls, include_extras=True).items():
        if hint is KW_ONLY:
            kw_only = True
            continue
        if name.startswith("_") or hint is ClassVar or get_origin(hint) is ClassVar:
            continue
        kind, capacity = classify(name, hint)
        specs.append(FieldSpec(name, len(specs), kind, offset, capacity, kw_only))
        span = capacity + (4 if kind in PREFIXED else 0)
        offset += -(-span // ALIGN) * ALIGN
    if not specs:
        raise TypeError(f"{cls.__qualname__} declares no fields")
    if len(specs) > MAX_FIELDS:
        raise TypeError(f"{cls.__qualname__} declares {len(specs)} fields; the limit is {MAX_FIELDS}")
    identity = "|".join([class_identity(cls), *(f"{s.name}:{s.kind}:{s.capacity}" for s in specs)])
    schema_hash = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "little")
    return Layout(tuple(specs), offset, schema_hash, {s.name: s for s in specs})
