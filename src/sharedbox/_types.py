"""How each field type is stored: kind code, size, description and schema text."""

from __future__ import annotations

import datetime
import decimal
import struct
import uuid
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Final,
    NewType,
    TypeGuard,
    get_args,
    get_origin,
)

if TYPE_CHECKING:
    from ._box import SharedBox

MAX_CAPACITY: Final[int] = 1 << 20
FIRST_DESCRIBED: Final[int] = 64
MAX_DEPTH: Final[int] = 16
MAX_COUNT: Final[int] = 65535
CODES: Final[dict[str, int]] = {
    "bool": 0,
    "int": 1,
    "float": 2,
    "str": 3,
    "bytes": 4,
    "ref": 5,
    "complex": 6,
    "date": 7,
    "time": 8,
    "datetime": 9,
    "timedelta": 10,
    "uuid": 11,
    "decimal": 12,
    "enum": 64,
    "flag": 65,
    "literal": 66,
    "optional": 67,
    "union": 68,
    "record": 69,
    "tuple": 70,
    "list": 71,
    "set": 72,
    "dict": 73,
    "array": 74,
}
PREFIXED: Final[frozenset[str]] = frozenset({"str", "bytes", "decimal"})
SUPPORTED: Final[str] = (
    "bool, int, float, complex, Annotated[str | bytes | bytearray | Decimal, Capacity(n)], "
    "date, time, datetime, timedelta, UUID, an Enum or Flag, a Literal, X | None, a union, "
    "a tuple, a NamedTuple, a dataclass, a TypedDict, an attrs class, a msgspec Struct, "
    "Annotated[list | set | frozenset | dict | tuple[T, ...], Capacity(n)] and their "
    "collections.abc forms, Annotated[<array type>, Shape(...), DType(...)], or a SharedBox "
    "subclass, optionally with | None"
)


@dataclass(frozen=True)
class Capacity:
    """Maximum size of a field: bytes for `str`, `bytes`, `bytearray` and `Decimal`, elements for a collection.

    It goes in the annotation, as in `Annotated[str, Capacity(32)]` or
    `Annotated[list[int], Capacity(16)]`, and allows 1 to 1048576 (1 Mi).

    Raises
    ------
    ValueError
        If `size` is not between 1 and 1 Mi.
    """

    size: int
    """Bytes for text and bytes, not characters: a UTF-8 character can take up to 4 bytes; elements for a collection."""

    def __post_init__(self) -> None:
        if not 0 < self.size <= MAX_CAPACITY:
            raise ValueError(
                f"capacity must be between 1 and {MAX_CAPACITY}, got {self.size}"
            )


def is_box_class(hint: object) -> TypeGuard[type[SharedBox]]:
    # A SharedBox subclass has its identity before its layout is built, so a class
    # that refers to itself passes too.
    return isinstance(hint, type) and hasattr(hint, "__sharedbox_identity__")


def round_up(value: int, unit: int) -> int:
    return -(-value // unit) * unit


@dataclass(frozen=True, eq=False)
class TypeSpec:
    """How one field or member is stored, and what reading it back needs."""

    kind: str
    """A key of `CODES`."""
    size: int
    """Bytes the value takes in the record, a length prefix included."""
    alignment: int
    text: str
    """This type's part of the schema text."""
    capacity: int = 0
    """`str`, `bytes`, `Decimal`: most bytes; list, set, dict: most elements."""
    members: tuple[Member, ...] = ()
    count: int = 0
    """The description's count: values, names or members."""
    before: bytes = b""
    """Description bytes between the head and the entries."""
    after: bytes = b""
    """Description bytes after the entries."""
    info: tuple[Any, ...] = ()
    """What the native module needs to convert values of a described type."""
    match: tuple[type, ...] = ()
    """The classes a value of this type has, which tell union members apart."""
    exact: type | None = None
    """A class a value may have exactly, which a union tries before the others."""
    hashable: bool = True
    """Whether a value read back can be a set element or a dict key."""
    bytearray: bool = False
    """A bytes value read back as `bytearray`."""

    @property
    def code(self) -> int:
        return CODES[self.kind]

    @property
    def described(self) -> bool:
        return self.code >= FIRST_DESCRIBED

    @property
    def entry_low(self) -> int:
        """The low 24 bits of a fixed kind's entry: its capacity if prefixed, else its size."""
        return self.capacity if self.kind in PREFIXED else self.size


@dataclass(frozen=True)
class Member:
    """One member of a described type: where it starts in the value, and how it is stored."""

    offset: int
    type: TypeSpec
    name: str = ""


def fixed(kind: str, size: int, alignment: int, cls: type, text: str | None = None) -> TypeSpec:
    return TypeSpec(kind, size, alignment, text or kind, match=(cls,), exact=cls)


SCALARS: Final[dict[type, TypeSpec]] = {
    bool: fixed("bool", 1, 1, bool, "bool:1"),
    int: fixed("int", 8, 8, int, "int:8"),
    float: fixed("float", 8, 8, float, "float:8"),
    complex: fixed("complex", 16, 8, complex),
    datetime.datetime: fixed("datetime", 16, 8, datetime.datetime),
    datetime.date: fixed("date", 4, 4, datetime.date),
    datetime.time: fixed("time", 16, 8, datetime.time),
    datetime.timedelta: fixed("timedelta", 12, 4, datetime.timedelta),
    uuid.UUID: fixed("uuid", 16, 1, uuid.UUID),
}
TEXT: Final[dict[type, str]] = {
    str: "str",
    bytes: "bytes",
    bytearray: "bytes",
    decimal.Decimal: "decimal",
}


def unsupported(where: str, hint: object) -> TypeError:
    return TypeError(f"{where}: unsupported annotation {hint!r}; use {SUPPORTED}")


def deeper(depth: int, where: str) -> int:
    """Return the depth of a described type's members, refusing nesting past `MAX_DEPTH`."""
    if depth > MAX_DEPTH:
        raise TypeError(f"{where}: types nest at most {MAX_DEPTH} levels deep")
    return depth + 1


def is_alias(hint: object) -> bool:
    """Whether `hint` is the alias a `type` statement makes, from `typing` or `typing_extensions`."""
    return type(hint).__name__ == "TypeAliasType" and hasattr(hint, "__value__")


def unwrap(
    hint: Any, where: str, path: frozenset[int]
) -> tuple[Any, list[Any], frozenset[int]]:
    """Strip `Final`, `Annotated`, `NewType` and type aliases off `hint`, keeping the `Annotated` extras.

    Raises
    ------
    TypeError
        For a bare `Final`, a generic alias given arguments, an alias that
        refers to itself, or an alias that names something undefined.
    """
    extras: list[Any] = []
    while True:
        origin = get_origin(hint)
        if origin is Final:
            (hint,) = get_args(hint)
        elif origin is Annotated:
            hint, *more = get_args(hint)
            extras += more
        elif hint is Final:
            raise TypeError(f"{where}: Final needs a type, as in Final[int]")
        elif isinstance(hint, NewType):
            hint = hint.__supertype__
        elif is_alias(hint):
            if id(hint) in path:
                raise TypeError(f"{where}: type alias {hint.__name__} refers to itself")
            path = path | {id(hint)}
            try:
                hint = hint.__value__
            except NameError as error:
                raise TypeError(f"{where}: {error.name!r} is not defined") from None
        elif is_alias(origin):
            raise TypeError(
                f"{where}: generic type alias {hint!r} is not supported; use its value"
            )
        else:
            return hint, extras, path


def parse(
    hint: Any,
    where: str,
    *,
    capacity: int | None = None,
    depth: int = 1,
    path: frozenset[int] = frozenset(),
) -> TypeSpec:
    """Return how a value annotated `hint` is stored.

    Parameters
    ----------
    where
        `Class.field`, with the member's place inside it, for messages.
    capacity
        A `Capacity` given on an annotation around this one, such as the
        `Optional` or union it is part of.
    depth
        Nesting level of a described type here, the field's own being 1.
    path
        Type aliases being unwrapped around this one.

    Raises
    ------
    TypeError
        If the annotation, or one inside it, is not one this version can
        store.
    """
    hint, extras, path = unwrap(hint, where, path)
    capacities = [extra.size for extra in extras if isinstance(extra, Capacity)]
    if len(capacities) + (capacity is not None) > 1:
        raise TypeError(f"{where}: more than one Capacity applies to {hint!r}")
    if capacities:
        capacity = capacities[0]
    return dispatch(hint, extras, where, capacity, depth, path)


def dispatch(
    hint: Any,
    extras: list[Any],
    where: str,
    capacity: int | None,
    depth: int,
    path: frozenset[int],
) -> TypeSpec:
    """Return the `TypeSpec` of an unwrapped annotation; `parse` documents the parameters."""
    if hint in TEXT:
        if capacity is None:
            raise unsupported(where, hint)
        kind = TEXT[hint]
        return TypeSpec(
            kind,
            4 + capacity,
            4,
            f"{kind}:{capacity}",
            capacity=capacity,
            match=(hint,),
            exact=hint,
            hashable=hint is not bytearray,
            bytearray=hint is bytearray,
        )
    if capacity is not None and not takes_capacity(hint):
        raise TypeError(f"{where}: Capacity does not apply to {hint!r}")
    if isinstance(hint, type) and hint in SCALARS:
        return SCALARS[hint]
    raise unsupported(where, hint)


def takes_capacity(hint: Any) -> bool:
    """Whether a `Capacity` on `hint` has a type to apply to."""
    return hint in TEXT


class Table:
    """The description table of one class, written as its fields are laid out."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.info: dict[int, tuple[Any, ...]] = {}
        self.bytearrays: list[tuple[int, int]] = []

    def entry(self, spec: TypeSpec, offset: int) -> bytes:
        """Return the 8-byte entry of a member at `offset`, writing its description first if it has one."""
        low = self.describe(spec) if spec.described else spec.entry_low
        return struct.pack("<II", offset, low | spec.code << 24)

    def describe(self, spec: TypeSpec) -> int:
        """Write the description of `spec`, then those of its members after it, and return its offset."""
        at = len(self.data)
        length = 8 + len(spec.before) + 8 * len(spec.members) + len(spec.after)
        self.data += bytes(round_up(length, 8))
        entries = b"".join(self.entry(m.type, m.offset) for m in spec.members)
        body = b"".join(
            [
                struct.pack("<BBHI", spec.code, 0, spec.count, spec.size),
                spec.before,
                entries,
                spec.after,
            ]
        )
        self.data[at : at + len(body)] = body
        self.info[at] = spec.info
        self.bytearrays += [
            (at, i) for i, m in enumerate(spec.members) if m.type.bytearray
        ]
        return at
