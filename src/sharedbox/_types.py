"""How each field type is stored: kind code, size, description and schema text."""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import struct
import sys
import types
import typing
import uuid
from collections import abc
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Final,
    Literal,
    NewType,
    NotRequired,
    Required,
    TypeGuard,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

if TYPE_CHECKING:
    from ._box import SharedBox

READ_ONLY: Final[Any] = getattr(typing, "ReadOnly", None)
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
    if hint is tuple or get_origin(hint) is tuple:
        args = get_args(hint)
        if len(args) == 2 and args[1] is Ellipsis:
            raise unsupported(where, hint)
        return tuple_spec(hint, where, depth, path)
    if isinstance(hint, type):
        spec = record_spec(hint, where, depth, path)
        if spec is not None:
            return spec
    if dataclasses.is_dataclass(get_origin(hint)) or is_typeddict(get_origin(hint)):
        raise TypeError(f"{where}: generic record {hint!r} is not supported")
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
        return optional_of(inner)
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


def optional_of(inner: TypeSpec) -> TypeSpec:
    """Return the optional around `inner`: a presence byte, padding to its alignment, then the value."""
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


def record_hints(cls: type, where: str) -> dict[str, Any]:
    """Return the resolved annotations of a record class, with `Annotated` extras kept."""
    try:
        return get_type_hints(cls, include_extras=True)
    except NameError as error:
        raise TypeError(
            f"{where}: {error.name!r} is not defined; a class named in a record's annotations must be defined first"
        ) from None


def lay_out(
    kind: str, items: list[tuple[str, TypeSpec]], where: str
) -> tuple[tuple[Member, ...], int, int]:
    """Place record members by descending alignment, as fields are; return them in declaration order, the size and the alignment."""
    if not 0 < len(items) <= 256:
        raise TypeError(f"{where}: a {kind} holds 1 to 256 members, not {len(items)}")
    offsets = [0] * len(items)
    end = 0
    for i in sorted(range(len(items)), key=lambda i: -items[i][1].alignment):
        end = round_up(end, items[i][1].alignment)
        offsets[i] = end
        end += items[i][1].size
    a = max(spec.alignment for _, spec in items)
    members = tuple(
        Member(offsets[i], spec, name) for i, (name, spec) in enumerate(items)
    )
    return members, round_up(end, a), a


def record_spec(
    hint: type, where: str, depth: int, path: frozenset[int]
) -> TypeSpec | None:
    """Return how instances of the record class `hint` are stored, or None if it is not one."""
    if is_typeddict(hint):
        form = 3
    elif issubclass(hint, tuple) and hasattr(hint, "_fields"):
        form = 1
    elif (
        dataclasses.is_dataclass(hint)
        or hasattr(hint, "__attrs_attrs__")
        or hasattr(hint, "__struct_fields__")
    ):
        form = 0
    else:
        return None
    if getattr(hint, "__parameters__", ()):
        raise TypeError(
            f"{where}: generic record class {hint.__qualname__} is not supported"
        )
    hints = record_hints(hint, where)
    below = deeper(depth, where)
    attrs: list[str] = []
    keywords: list[str] = []
    required: list[bool] = []
    items: list[tuple[str, TypeSpec]] = []
    if form == 3:
        qualifiers = {Required, NotRequired} | (
            {READ_ONLY} if READ_ONLY is not None else set()
        )
        for key, member in hints.items():
            needed = key in hint.__required_keys__  # type: ignore[attr-defined]
            while get_origin(member) in qualifiers:
                if get_origin(member) is Required:
                    needed = True
                elif get_origin(member) is NotRequired:
                    needed = False
                (member,) = get_args(member)
            spec = parse(
                member,
                f"{where}.{key}",
                depth=below if needed else deeper(below, where),
                path=path,
            )
            if not needed:
                spec = optional_of(spec)
            attrs.append(key)
            required.append(needed)
            items.append((key, spec))
    else:
        for name, keyword, member in record_members(hint, hints, form, where):
            attrs.append(name)
            keywords.append(keyword)
            items.append(
                (name, parse(member, f"{where}.{name}", depth=below, path=path))
            )
    members, size, alignment = lay_out("record", items, where)
    hashable = (
        form != 3
        and hint.__hash__ is not None
        and hint.__eq__ is not object.__eq__  # type: ignore[comparison-overlap]
        and all(m.type.hashable for m in members)
    )
    struct_config = getattr(hint, "__struct_config__", None)
    if struct_config is not None:
        hashable = hashable and bool(getattr(struct_config, "frozen", False))
    text = (
        "record("
        + ",".join(
            f"{m.name}{'' if form != 3 or req else '?'}:{m.type.text}"
            for m, req in zip(members, required or [True] * len(members), strict=True)
        )
        + ")"
    )
    return TypeSpec(
        "record",
        size,
        alignment,
        text,
        members=members,
        count=len(members),
        after=b"".join(name_bytes(m.name) for m in members),
        info=(
            form,
            None if form == 3 else hint,
            tuple(sys.intern(a) for a in attrs),
            tuple(sys.intern(k) for k in (keywords or attrs)),
            tuple(required),
        ),
        match=(abc.Mapping,) if form == 3 else (hint,),
        exact=None if form == 3 else hint,
        hashable=hashable,
    )


def record_members(
    hint: type, hints: dict[str, Any], form: int, where: str
) -> list[tuple[str, str, Any]]:
    """Return `(attribute, keyword, annotation)` of each stored member of a dataclass, attrs class, Struct or NamedTuple."""
    if form == 1:
        for name in hint._fields:  # type: ignore[attr-defined]
            if name not in hints:
                raise TypeError(f"{where}: {hint.__qualname__}.{name} has no type")
        return [(name, name, hints[name]) for name in hint._fields]  # type: ignore[attr-defined]
    if dataclasses.is_dataclass(hint):
        if not hint.__dataclass_params__.init:  # type: ignore[attr-defined]
            raise TypeError(
                f"{where}: {hint.__qualname__} has init=False, so a read could not rebuild it"
            )
        for name, member in hints.items():
            if isinstance(member, dataclasses.InitVar) and not hasattr(hint, name):
                raise TypeError(
                    f"{where}: InitVar {name!r} of {hint.__qualname__} has no default, so a read could not rebuild it"
                )
        found = []
        for f in dataclasses.fields(hint):
            if not f.init:
                raise TypeError(
                    f"{where}: {hint.__qualname__}.{f.name} has init=False, so a read could not rebuild it"
                )
            found.append((f.name, f.name, hints[f.name]))
        return found
    if hasattr(hint, "__attrs_attrs__"):
        if "__init__" not in vars(hint):
            raise TypeError(
                f"{where}: {hint.__qualname__} has init=False, so a read could not rebuild it"
            )
        found = []
        for a in hint.__attrs_attrs__:
            if not a.init:
                raise TypeError(
                    f"{where}: {hint.__qualname__}.{a.name} has init=False, so a read could not rebuild it"
                )
            member = hints.get(a.name, a.type)
            if member is None:
                raise TypeError(f"{where}: {hint.__qualname__}.{a.name} has no type")
            keyword = getattr(a, "alias", None) or a.name.lstrip("_")
            found.append((a.name, keyword, member))
        return found
    return [(name, name, hints[name]) for name in hint.__struct_fields__]  # type: ignore[attr-defined]


def tuple_spec(hint: Any, where: str, depth: int, path: frozenset[int]) -> TypeSpec:
    """Return how a fixed-length `tuple[A, B, ...]` is stored."""
    args = get_args(hint)
    if not args or args == ((),):
        raise unsupported(where, hint)
    below = deeper(depth, where)
    items = [
        (str(i), parse(arg, f"{where}[{i}]", depth=below, path=path))
        for i, arg in enumerate(args)
    ]
    members, size, alignment = lay_out("tuple", items, where)
    members = tuple(Member(m.offset, m.type) for m in members)
    return TypeSpec(
        "tuple",
        size,
        alignment,
        "tuple(" + ",".join(m.type.text for m in members) + ")",
        members=members,
        count=len(members),
        info=(2, None, (), (), ()),
        match=(tuple,),
        exact=tuple,
        hashable=all(m.type.hashable for m in members),
    )


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
