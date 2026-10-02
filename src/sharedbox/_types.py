"""How each field type is stored: kind code, size, description and schema text."""

from __future__ import annotations

import datetime
import decimal
import enum
import struct
import types
import uuid
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Final,
    Literal,
    NewType,
    TypeGuard,
    Union,
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


def fixed(
    kind: str, size: int, alignment: int, cls: type, text: str | None = None
) -> TypeSpec:
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
    kind = type(hint)
    return kind.__name__ == "TypeAliasType" and hasattr(kind, "__value__")


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
    if isinstance(hint, type) and hint in TEXT:
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
    if get_origin(hint) in (Union, types.UnionType):
        if capacity is not None and not takes_capacity(hint, where, path):
            raise TypeError(f"{where}: Capacity does not apply to {hint!r}")
        return union_spec(hint, where, capacity, depth, path)
    if is_box_class(hint):
        raise TypeError(
            f"{where}: a reference to a box must be a field of its own, annotated X or X | None, not part of another type"
        )
    if capacity is not None and not takes_capacity(hint, where, path):
        raise TypeError(f"{where}: Capacity does not apply to {hint!r}")
    if isinstance(hint, type) and hint in SCALARS:
        return SCALARS[hint]
    if isinstance(hint, type) and issubclass(hint, enum.Flag):
        return flag_spec(hint, where)
    if isinstance(hint, type) and issubclass(hint, enum.Enum):
        return enum_spec(hint, where)
    if get_origin(hint) is Literal:
        return literal_spec(hint, where)
    raise unsupported(where, hint)


def name_bytes(text: str) -> bytes:
    """`text` as a description stores a name: a u16 length, then UTF-8."""
    raw = text.encode()
    if len(raw) > MAX_COUNT:
        raise TypeError(f"a name of {len(raw)} bytes does not fit a description")
    return struct.pack("<H", len(raw)) + raw


def enum_spec(hint: type[enum.Enum], where: str) -> TypeSpec:
    members = tuple(hint)
    if not 0 < len(members) <= MAX_COUNT:
        raise TypeError(
            f"{where}: an enum needs 1 to {MAX_COUNT} members; {hint.__name__} has {len(members)}"
        )
    return TypeSpec(
        "enum",
        2,
        2,
        "enum(" + ",".join(m.name for m in members) + ")",
        count=len(members),
        after=b"".join(name_bytes(m.name) for m in members),
        info=(hint, members),
        match=(hint,),
        exact=hint,
    )


def flag_spec(hint: type[enum.Flag], where: str) -> TypeSpec:
    members = tuple(hint)
    if not 0 < len(members) <= 64:
        raise TypeError(
            f"{where}: a flag needs 1 to 64 members; {hint.__name__} has {len(members)}"
        )
    for m in members:
        if not isinstance(m.value, int) or not 0 <= m.value < 1 << 64:
            raise TypeError(
                f"{where}: flag member {m.name} needs an int value of 0 to 2**64 - 1"
            )
    return TypeSpec(
        "flag",
        8,
        8,
        "flag(" + ",".join(f"{m.name}={m.value}" for m in members) + ")",
        count=len(members),
        after=b"".join(
            name_bytes(str(m.name)) + struct.pack("<Q", m.value) for m in members
        ),
        info=(hint,),
        match=(hint,),
        exact=hint,
    )


def literal_spec(hint: Any, where: str) -> TypeSpec:
    values = get_args(hint)
    if not 0 < len(values) <= MAX_COUNT:
        raise TypeError(f"{where}: a Literal needs 1 to {MAX_COUNT} values")
    parts: list[bytes] = []
    texts: list[str] = []
    for value in values:
        if value is None:
            parts.append(b"\x00")
            texts.append("None")
        elif isinstance(value, bool):
            parts.append(struct.pack("<BB", 1, value))
            texts.append(repr(value))
        elif isinstance(value, enum.Enum):
            parts.append(b"\x05" + name_bytes(value.name))
            texts.append(f"member:{value.name}")
        elif isinstance(value, int):
            if not -(1 << 63) <= value < 1 << 63:
                raise TypeError(
                    f"{where}: Literal value {value} does not fit a signed 64-bit integer"
                )
            parts.append(struct.pack("<Bq", 2, value))
            texts.append(f"int:{value}")
        elif isinstance(value, str):
            parts.append(b"\x03" + name_bytes(value))
            texts.append(f"str:{value!r}")
        elif isinstance(value, bytes):
            parts.append(struct.pack("<BI", 4, len(value)) + value)
            texts.append(f"bytes:{value!r}")
        else:
            raise TypeError(
                f"{where}: Literal value {value!r} is not None, a bool, an int, a str, bytes or an enum member"
            )
    return TypeSpec(
        "literal",
        2,
        2,
        "literal(" + ",".join(texts) + ")",
        count=len(values),
        after=b"".join(parts),
        info=(values,),
        match=tuple(dict.fromkeys(type(value) for value in values)),
    )


NoneType = type(None)


def narrower(a: TypeSpec, b: TypeSpec) -> bool:
    """Whether some class `a` matches is a strict subclass of one `b` matches."""
    return any(x is not y and issubclass(x, y) for x in a.match for y in b.match)


def union_spec(
    hint: Any, where: str, capacity: int | None, depth: int, path: frozenset[int]
) -> TypeSpec:
    args = get_args(hint)
    rest = tuple(arg for arg in args if arg is not NoneType)
    below = deeper(depth, where)
    if len(rest) < len(args):
        inner = parse(
            rest[0] if len(rest) == 1 else Union[rest],  # noqa: UP007
            where,
            capacity=capacity,
            depth=below,
            path=path,
        )
        if inner.kind == "array":
            raise TypeError(
                f"{where}: an array can be a field or a record member, not optional"
            )
        a = inner.alignment
        return TypeSpec(
            "optional",
            round_up(a + inner.size, a),
            a,
            f"optional({inner.text})",
            members=(Member(a, inner),),
            count=1,
            match=(*inner.match, NoneType),
            hashable=inner.hashable,
        )
    takers = [arg for arg in rest if takes_capacity(arg, where, path)]
    if capacity is not None and len(takers) != 1:
        raise TypeError(
            f"{where}: a Capacity on a union needs exactly one member it applies to; {hint!r} has {len(takers)}"
        )
    members = [
        parse(
            arg,
            where,
            capacity=capacity if arg is (takers or [None])[0] else None,
            depth=below,
            path=path,
        )
        for arg in rest
    ]
    if len(members) > 255:
        raise TypeError(f"{where}: a union holds at most 255 members")
    for m in members:
        if m.kind == "array":
            raise TypeError(
                f"{where}: an array can be a field or a record member, not a union member"
            )
    for i, m in enumerate(members):
        for other in members[:i]:
            shared = set(m.match) & set(other.match)
            if shared:
                raise TypeError(
                    f"{where}: two union members take values of the same class, {sorted(c.__name__ for c in shared)}; "
                    "the stored value could not tell which was meant"
                )
    order = sorted(
        range(len(members)),
        key=lambda i: (
            -sum(
                narrower(members[i], members[j]) for j in range(len(members)) if j != i
            )
        ),
    )
    a = max(m.alignment for m in members)
    largest = max(m.size for m in members)
    return TypeSpec(
        "union",
        round_up(a + largest, a),
        a,
        "union(" + ",".join(m.text for m in members) + ")",
        members=tuple(Member(a, m) for m in members),
        count=len(members),
        info=(tuple(order), tuple(m.exact for m in members)),
        match=tuple(dict.fromkeys(c for m in members for c in m.match)),
        hashable=all(m.hashable for m in members),
    )


def takes_capacity(hint: Any, where: str, path: frozenset[int]) -> bool:
    """Whether a `Capacity` on `hint` has a type to apply to."""
    hint, _, path = unwrap(hint, where, path)
    if isinstance(hint, type) and hint in TEXT:
        return True
    if get_origin(hint) in (Union, types.UnionType):
        return any(
            takes_capacity(arg, where, path)
            for arg in get_args(hint)
            if arg is not NoneType
        )
    return False


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
