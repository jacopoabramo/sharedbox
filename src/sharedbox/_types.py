"""How each field type is stored: kind code, size, description and schema text."""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import inspect
import math
import struct
import sys
import types
import typing
import uuid
from collections import abc
from dataclasses import MISSING, dataclass
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
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

from ._arrays import (
    CONVERTERS,
    DLPACK,
    DType,
    Shape,
    SupportsDLPack,
    converter,
    dtype_name,
    key,
)
from ._native import NativeCollection, NativeSet

if TYPE_CHECKING:
    from collections.abc import Callable

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
    "int8": 13,
    "int16": 14,
    "int32": 15,
    "uint8": 16,
    "uint16": 17,
    "uint32": 18,
    "uint64": 19,
    "float16": 20,
    "float32": 21,
    "complex64": 22,
    "datetime64": 23,
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
    "bool, int, float, complex, a numpy scalar type: bool_, int8 to int64, uint8 to uint64, "
    "float16 to float64, complex64, complex128 or datetime64, "
    "Annotated[str | bytes | bytearray | Decimal, Capacity(n)], "
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
    A box field can take the same number from `field(capacity=...)`
    instead.

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
    reusable: bool = False
    """Whether a value read back cannot be changed, so a later read may return it again."""
    read_as: Callable[[Any], Any] | None = None
    """Called on each value read back, to give the declared type."""
    write_as: Callable[[Any], Any] | None = None
    """Called on each value before it is stored, to give one the kind takes."""

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
    return TypeSpec(
        kind, size, alignment, text or kind, match=(cls,), exact=cls, reusable=True
    )


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
NUMPY_SCALARS: Final[dict[str, tuple[str, int, int, str]]] = {
    "bool": ("bool", 1, 1, "bool:1"),
    "int64": ("int", 8, 8, "int:8"),
    "float64": ("float", 8, 8, "float:8"),
    "complex128": ("complex", 16, 8, "complex"),
    "int8": ("int8", 1, 1, "int8"),
    "int16": ("int16", 2, 2, "int16"),
    "int32": ("int32", 4, 4, "int32"),
    "uint8": ("uint8", 1, 1, "uint8"),
    "uint16": ("uint16", 2, 2, "uint16"),
    "uint32": ("uint32", 4, 4, "uint32"),
    "uint64": ("uint64", 8, 8, "uint64"),
    "float16": ("float16", 2, 2, "float16"),
    "float32": ("float32", 4, 4, "float32"),
    "complex64": ("complex64", 8, 4, "complex64"),
    "datetime64": ("datetime64", 8, 8, "datetime64"),
}
"""A numpy scalar type's dtype name -> its kind, size, alignment and schema text."""
TEXT: Final[dict[type, str]] = {
    str: "str",
    bytes: "bytes",
    bytearray: "bytes",
    decimal.Decimal: "decimal",
}

LISTS: Final[dict[Any, type]] = {
    list: list,
    abc.Sequence: list,
    abc.MutableSequence: list,
}
SETS: Final[dict[Any, type]] = {
    set: set,
    frozenset: frozenset,
    abc.Set: set,
    abc.MutableSet: set,
}
DICTS: Final[dict[Any, type]] = {
    dict: dict,
    abc.Mapping: dict,
    abc.MutableMapping: dict,
}


def numpy_spec(hint: Any) -> TypeSpec | None:
    """Return how values of the numpy scalar type `hint` are stored, or None if it is not one."""
    if not isinstance(hint, type) or hint.__module__ != "numpy":
        return None
    found = NUMPY_SCALARS.get(dtype_name(hint))
    if found is None:
        return None
    kind, size, alignment, text = found
    timed = kind == "datetime64"
    return TypeSpec(
        kind,
        size,
        alignment,
        text,
        match=(hint,),
        exact=hint,
        reusable=True,
        read_as=(lambda ns: hint(ns, "ns")) if timed else hint,
        write_as=datetime64_ns if timed else None,
    )


NS_PER_UNIT: Final[dict[str, int]] = {
    "W": 7 * 86_400 * 10**9,
    "D": 86_400 * 10**9,
    "h": 3_600 * 10**9,
    "m": 60 * 10**9,
    "s": 10**9,
    "ms": 10**6,
    "us": 10**3,
    "ns": 1,
}
UNITS_PER_NS: Final[dict[str, int]] = {"ps": 10**3, "fs": 10**6, "as": 10**9}
NAT: Final[int] = -(2**63)


def datetime64_ns(value: Any) -> int:
    """Return the nanoseconds since 1970-01-01T00:00 of the numpy datetime64 `value`, `-2**63` for NaT.

    Raises
    ------
    TypeError
        If `value` is not a numpy datetime64.
    OverflowError
        If `value` is before 1678 or after 2262, which nanoseconds in 64 bits cannot hold.
    """
    numpy = sys.modules.get("numpy")
    if numpy is None or not isinstance(value, numpy.datetime64):
        raise TypeError(f"expects a numpy.datetime64, got {type(value).__name__}")
    if numpy.isnat(value):
        return NAT
    unit, step = numpy.datetime_data(value.dtype)
    count = int(value.astype(numpy.int64)) * step
    if unit in ("Y", "M"):
        # Years and months have no fixed length, so they go through days, which numpy computes in 64 bits.
        if abs(count) > (1000 if unit == "Y" else 12_000):
            raise OverflowError(f"holds times from 1678 to 2262; {value} does not fit")
        ns = int(value.astype("datetime64[D]").astype(numpy.int64)) * NS_PER_UNIT["D"]
    elif unit in UNITS_PER_NS:
        ns = count // UNITS_PER_NS[unit]
    elif unit in NS_PER_UNIT:
        ns = count * NS_PER_UNIT[unit]
    else:
        raise TypeError(f"expects a numpy.datetime64 with a unit, got {value!r}")
    if not NAT < ns < -NAT:
        raise OverflowError(f"holds times from 1678 to 2262; {value} does not fit")
    return ns


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
            try:
                value = origin.__value__
            except NameError:
                value = None
            if not is_array_type(value):
                raise TypeError(
                    f"{where}: generic type alias {hint!r} is not supported; use its value"
                )
            # numpy.typing.NDArray is a generic type alias from Python 3.12 on.
            try:
                hint = value[get_args(hint)]
            except TypeError as error:
                raise TypeError(f"{where}: {hint!r}: {error}") from None
        else:
            return hint, extras, path


def parse(
    hint: Any,
    where: str,
    *,
    capacity: int | None = None,
    depth: int = 1,
    path: frozenset[int] = frozenset(),
    sizes: tuple[tuple[str, tuple[int, ...]] | None, Any] | None = None,
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
    sizes
        The `field(shape=...)` of a box field, as the name a message gives it
        and its sizes, and the field's plain default, which an array field,
        or the array of an optional field, takes its sizes from when the
        annotation has no `Shape`. A record member passes its metadata shape
        and no default. None when neither is given.

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
    if any(isinstance(extra, (Shape, DType)) for extra in extras) or is_array_type(
        hint
    ):
        if capacity is not None:
            raise TypeError(
                f"{where}: Capacity does not apply to an array; give its Shape"
            )
        spec = array_spec(hint, extras, where, sizes)
    else:
        spec = dispatch(hint, extras, where, capacity, depth, path, sizes)
        holds_array = spec.kind == "optional" and spec.members[0].type.kind == "array"
        if sizes is not None and sizes[0] is not None and not holds_array:
            raise TypeError(f"{where}: a shape applies only to an array")
    if spec.described:
        deeper(depth, where)
    return spec


def is_array_type(hint: Any) -> bool:
    origin = get_origin(hint) or hint
    return isinstance(origin, type) and (
        origin is SupportsDLPack
        or key(origin) in CONVERTERS
        or key(origin) in ("numpy.ndarray", "torch.Tensor")
    )


def annotation_dtype(hint: Any, where: str) -> str | None:
    """Return the dtype a parametrised array annotation names, as in `NDArray[np.float32]`; None if it names none.

    Raises
    ------
    TypeError
        If it names a concrete scalar type DLPack cannot describe.
    """
    args = get_args(hint)
    if len(args) != 2:
        return None
    inner = get_args(args[1])
    # Any and an abstract scalar type such as numpy.floating, which has subclasses, name no one dtype.
    if (
        len(inner) != 1
        or not isinstance(inner[0], type)
        or inner[0] is Any
        or inner[0].__subclasses__()
    ):
        return None
    name = dtype_name(inner[0])
    if name not in DLPACK:
        raise TypeError(
            f"{where}: unsupported array element type {name}; use one of {', '.join(DLPACK)}"
        )
    return name


def annotation_ndim(hint: Any) -> int | None:
    """Return the number of dimensions a parametrised array annotation names, as in `ndarray[tuple[int, int], ...]`; None if it names none."""
    args = get_args(hint)
    if len(args) != 2 or get_origin(args[0]) is not tuple:
        return None
    dims = get_args(args[0])
    if not dims or any(
        dim is Ellipsis
        or getattr(dim, "__unpacked__", False)
        or get_origin(dim) is typing.Unpack
        for dim in dims
    ):
        return None
    return len(dims)


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def shape_text(dims: tuple[int, ...]) -> str:
    return f"({', '.join(map(str, dims))})"


def given_sizes(
    extras: list[Any],
    where: str,
    option: tuple[str, tuple[int, ...]] | None,
    default: Any,
) -> list[tuple[str, tuple[int, ...]]]:
    """Return the sizes an array field is given, each with the name of where it came from."""
    shapes = [extra for extra in extras if isinstance(extra, Shape)]
    if len(shapes) > 1:
        raise TypeError(f"{where}: an array field takes one Shape")
    given = [(f"Shape{shape_text(shape.dims)}", shape.dims) for shape in shapes]
    if option is not None:
        given.append(option)
    shape = getattr(default, "shape", None)
    if (
        default is not MISSING
        and isinstance(shape, tuple)
        and all(type(dim) is int for dim in shape)
    ):
        shape = tuple(shape)
        if not 1 <= len(shape) <= 8 or min(shape) < 1:
            raise TypeError(
                f"{where}: the default array has shape {shape_text(shape)}; an array field has 1 to 8 dimensions of at least 1"
            )
        given.append((f"the default array's shape {shape_text(shape)}", shape))
    return given


def array_spec(
    hint: Any,
    extras: list[Any],
    where: str,
    sizes: tuple[tuple[str, tuple[int, ...]] | None, Any] | None = None,
) -> TypeSpec:
    dtypes = [extra.name for extra in extras if isinstance(extra, DType)]
    option, default = sizes or (None, MISSING)
    given = given_sizes(extras, where, option, default)
    if not given:
        ways = (
            "a Shape in the annotation, as in Annotated[numpy.ndarray, Shape(2, 3), DType('float32')]"
            + (
                ", from field(shape=(2, 3)), or from the shape of a default array"
                if sizes is not None
                else ""
            )
        )
        raise TypeError(f"{where}: an array field needs its sizes from {ways}")
    for label, dims in given[1:]:
        if dims != given[0][1]:
            raise TypeError(f"{where}: {given[0][0]} and {label} disagree")
    label, dims = given[0]
    ndim = annotation_ndim(hint)
    if ndim is not None and ndim != len(dims):
        raise TypeError(
            f"{where}: the annotation names {plural(ndim, 'dimension')} and {label} has {len(dims)}"
        )
    named = annotation_dtype(hint, where)
    if len(dtypes) > 1 or (not dtypes and named is None):
        raise TypeError(
            f"{where}: an array field needs one DType, or a dtype in its annotation"
        )
    name = dtypes[0] if dtypes else named
    assert name is not None
    if named is not None and named != name:
        raise TypeError(
            f"{where}: the annotation's dtype {named} and DType({name!r}) disagree"
        )
    code, bits = DLPACK[name]
    size = math.prod(dims) * bits // 8
    if size >= 1 << 32:
        raise TypeError(
            f"{where}: the array takes {size} bytes; a record holds less than 4 GiB"
        )
    origin = get_origin(hint) or hint
    convert, as_uint16 = converter(origin, name, where)
    return TypeSpec(
        "array",
        size,
        64,
        f"array({name},{'x'.join(map(str, dims))})",
        after=struct.pack("<BBHB3x", code, bits, 1, len(dims))
        + struct.pack(f"<{len(dims)}Q", *dims),
        info=(convert, as_uint16),
        match=(origin,),
        hashable=False,
    )


def dispatch(
    hint: Any,
    extras: list[Any],
    where: str,
    capacity: int | None,
    depth: int,
    path: frozenset[int],
    sizes: tuple[tuple[str, tuple[int, ...]] | None, Any] | None = None,
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
            reusable=hint is not bytearray,
        )
    if is_collection(hint):
        return collection_spec(hint, where, capacity, depth, path)
    if get_origin(hint) in (Union, types.UnionType):
        if capacity is not None and not takes_capacity(hint, where, path):
            raise TypeError(f"{where}: Capacity does not apply to {hint!r}")
        return union_spec(hint, where, capacity, depth, path, sizes)
    if is_box_class(hint):
        raise TypeError(
            f"{where}: a reference to a box must be a field of its own, annotated X or X | None, not part of another type"
        )
    if capacity is not None and not takes_capacity(hint, where, path):
        raise TypeError(f"{where}: Capacity does not apply to {hint!r}")
    if isinstance(hint, type) and hint in SCALARS:
        return SCALARS[hint]
    if (spec := numpy_spec(hint)) is not None:
        return spec
    if isinstance(hint, type) and issubclass(hint, enum.Flag):
        return flag_spec(hint, where)
    if isinstance(hint, type) and issubclass(hint, enum.Enum):
        return enum_spec(hint, where)
    if get_origin(hint) is Literal:
        return literal_spec(hint, where)
    if hint is tuple or get_origin(hint) is tuple:
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
        reusable=True,
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
        reusable=True,
    )


def literal_spec(hint: Any, where: str) -> TypeSpec:
    values = get_args(hint)
    if not 0 < len(values) <= MAX_COUNT:
        raise TypeError(f"{where}: a Literal needs 1 to {MAX_COUNT} values")
    parts: list[bytes] = []
    texts: list[str] = []
    for value in values:
        match value:
            case None:
                parts.append(b"\x00")
                texts.append("None")
            case bool():
                parts.append(struct.pack("<BB", 1, value))
                texts.append(repr(value))
            case enum.Enum():
                parts.append(b"\x05" + name_bytes(value.name))
                texts.append(f"member:{value.name}")
            case int():
                if not -(1 << 63) <= value < 1 << 63:
                    raise TypeError(
                        f"{where}: Literal value {value} does not fit a signed 64-bit integer"
                    )
                parts.append(struct.pack("<Bq", 2, value))
                texts.append(f"int:{value}")
            case str():
                parts.append(b"\x03" + name_bytes(value))
                texts.append(f"str:{value!r}")
            case bytes():
                parts.append(struct.pack("<BI", 4, len(value)) + value)
                texts.append(f"bytes:{value!r}")
            case _:
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
        reusable=True,
    )


NoneType = type(None)


def narrower(a: TypeSpec, b: TypeSpec) -> bool:
    """Whether some class `a` matches is a strict subclass of one `b` matches."""
    return any(x is not y and issubclass(x, y) for x in a.match for y in b.match)


def union_spec(
    hint: Any,
    where: str,
    capacity: int | None,
    depth: int,
    path: frozenset[int],
    sizes: tuple[tuple[str, tuple[int, ...]] | None, Any] | None = None,
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
            sizes=sizes if len(rest) == 1 else None,
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
        reusable=all(m.reusable for m in members),
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
        reusable=inner.reusable,
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
    return is_collection(hint)


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


def frozen(hint: type, form: int) -> bool:
    """Whether instances of the record class `hint` refuse every attribute assignment."""
    if form == 1:
        # A NamedTuple subclass that does not set __slots__ gives its instances a __dict__.
        return hint.__dictoffset__ == 0
    if form != 0:
        return False
    if dataclasses.is_dataclass(hint):
        # A plain subclass inherits the parameters but its generated __setattr__ still lets
        # it add attributes.
        return "__dataclass_params__" in vars(hint) and bool(
            hint.__dataclass_params__.frozen  # type: ignore[attr-defined]
        )
    if hasattr(hint, "__attrs_attrs__"):
        # An attrs version without __attrs_props__ cannot say, so its classes count as changeable.
        props = getattr(hint, "__attrs_props__", None)
        return bool(getattr(props, "is_frozen", False))
    return bool(getattr(hint.__struct_config__, "frozen", False))  # type: ignore[attr-defined]


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
        for name, keyword, member, shape in record_members(hint, hints, form, where):
            attrs.append(name)
            keywords.append(keyword)
            dims = None if shape is None else Shape(*shape).dims
            sizes = (
                None
                if dims is None
                else ((f"metadata shape {shape_text(dims)}", dims), MISSING)
            )
            items.append(
                (
                    name,
                    parse(
                        member, f"{where}.{name}", depth=below, path=path, sizes=sizes
                    ),
                )
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
            form == 0 and by_position(hint, keywords),
        ),
        match=(abc.Mapping,) if form == 3 else (hint,),
        exact=None if form == 3 else hint,
        hashable=hashable,
        reusable=frozen(hint, form) and all(m.type.reusable for m in members),
    )


def by_position(hint: type, keywords: list[str]) -> bool:
    """Whether calling `hint` with the members in order, each by position, passes each as its keyword."""
    try:
        parameters = list(inspect.signature(hint).parameters.values())
    except (TypeError, ValueError):
        return False
    return len(parameters) >= len(keywords) and all(
        p.kind is p.POSITIONAL_OR_KEYWORD and p.name == k
        for p, k in zip(parameters, keywords, strict=False)
    )


def record_members(
    hint: type, hints: dict[str, Any], form: int, where: str
) -> list[tuple[str, str, Any, Any]]:
    """Return `(attribute, keyword, annotation, shape)` of each stored member of a dataclass, attrs class, Struct or NamedTuple.

    The shape is the `"shape"` entry of a dataclass field's or attrs
    attribute's metadata, or None.
    """
    if form == 1:
        for name in hint._fields:  # type: ignore[attr-defined]
            if name not in hints:
                raise TypeError(f"{where}: {hint.__qualname__}.{name} has no type")
        return [(name, name, hints[name], None) for name in hint._fields]  # type: ignore[attr-defined]
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
            found.append((f.name, f.name, hints[f.name], f.metadata.get("shape")))
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
            found.append((a.name, keyword, member, a.metadata.get("shape")))
        return found
    return [(name, name, hints[name], None) for name in hint.__struct_fields__]  # type: ignore[attr-defined]


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
        reusable=all(m.type.reusable for m in members),
    )


def is_collection(hint: Any) -> bool:
    origin = get_origin(hint)
    variadic = (
        origin is tuple and len(get_args(hint)) == 2 and get_args(hint)[1] is Ellipsis
    )
    return (
        variadic
        or origin in LISTS
        or origin in SETS
        or origin in DICTS
        or bounded_class(hint) is not None
    )


BOUNDED: Final[tuple[type, ...]] = (NativeCollection, NativeSet)
FAMILIES: Final[dict[str, str]] = {
    **dict.fromkeys(
        (
            "bool",
            "int",
            "int8",
            "int16",
            "int32",
            "uint8",
            "uint16",
            "uint32",
            "uint64",
            "float",
            "float16",
            "float32",
            "complex",
            "complex64",
        ),
        "number",
    ),
    "str": "text",
    "bytes": "byte string",
    "date": "date",
}
"""Kind -> its equality family: values of two kinds of one family may compare equal, as `1 == 1.0 == True`."""
MIXIN_FAMILIES: Final[tuple[tuple[type, str], ...]] = (
    (int, "number"),
    (float, "number"),
    (complex, "number"),
    (str, "text"),
    (bytes, "byte string"),
    (tuple, "tuple"),
    (datetime.date, "date"),
)
"""The mixin type of an Enum or Flag -> the equality family of its members, which equal values of that type."""
INEXACT_KINDS: Final[frozenset[str]] = frozenset(
    {
        "float",
        "float16",
        "float32",
        "complex",
        "complex64",
        "datetime",
        "time",
        "datetime64",
    }
)
"""The kinds whose stored bytes a key check rewrites or refuses."""


def bounded_class(hint: Any) -> type | None:
    """Return the strict subclass of `NativeCollection` or `NativeSet` that `hint` is, alone or parametrised, or None."""
    origin = get_origin(hint) or hint
    if (
        isinstance(origin, type)
        and issubclass(origin, BOUNDED)
        and origin not in BOUNDED
    ):
        return origin
    return None


def bounded_args(hint: Any) -> tuple[Any, ...] | None:
    """Return the type arguments of a `NativeCollection` or `NativeSet` subclass, alone or parametrised.

    A class alone gives the arguments of the parametrised subclass it
    derives from, as `class Points(BoundedList[float])` gives `(float,)`.
    None when `hint` gives none, or one is a type variable.
    """
    bases = (
        base
        for cls in (get_origin(hint) or hint).__mro__
        for base in vars(cls).get("__orig_bases__", ())
        if bounded_class(base) is not None
    )
    args = get_args(hint) or get_args(next(bases, None))
    if not args or any(isinstance(arg, TypeVar) for arg in args):
        return None
    return args


def mixin_families(cls: type) -> set[str]:
    """Return the equality families of the members of the Enum or Flag `cls`, from its mixin type."""
    # A datetime never equals a date, so its members are a family apart from the date mixin's.
    if issubclass(cls, datetime.datetime):
        return {"datetime"}
    return {family for base, family in MIXIN_FAMILIES if issubclass(cls, base)}


def value_families(value: object) -> set[str]:
    """Return the equality families of a Literal value; an Enum member's come from its class's mixin type."""
    match value:
        case enum.Enum():
            return mixin_families(type(value))
        case int():
            return {"number"}
        case str():
            return {"text"}
        case bytes():
            return {"byte string"}
        case _:
            return set()


def families(spec: TypeSpec) -> set[str]:
    """Return the equality families of `spec`'s values; a tuple and a NamedTuple are of the family "tuple".

    An Enum or Flag takes the family of its mixin type, so a plain one,
    whose members equal no other value, has none. A Literal takes the
    family of each value, and a union or optional those of its members.
    """
    match spec.kind:
        case "enum" | "flag":
            return mixin_families(spec.info[0])
        case "literal":
            return {f for v in spec.info[0] for f in value_families(v)}
        case "record" | "tuple":
            return {"tuple"} if spec.info[0] in (1, 2) else set()
        case "list":
            return {"tuple"} if spec.info[0] is tuple else set()
        case "union" | "optional":
            return {f for m in spec.members for f in families(m.type)}
        case kind:
            return {FAMILIES[kind]} if kind in FAMILIES else set()


def key_refusal(spec: TypeSpec, inside: str | None = None) -> str | None:
    """Return what a key of `spec` cannot hold, because equal keys could differ in bytes, or None.

    `inside` names the union or `tuple[T, ...]` around `spec`, whose
    members a key check cannot reach.
    """
    if spec.kind == "decimal":
        return "a Decimal"
    if spec.kind == "set":
        return "a frozenset"
    if inside is not None and spec.kind in INEXACT_KINDS:
        return f"a {spec.text} inside {inside}"
    if spec.kind == "literal":
        values = spec.info[0]
        for i, value in enumerate(values):
            for earlier in values[:i]:
                if earlier == value:
                    return f"a Literal of equal values {earlier!r} and {value!r}"
    if spec.kind == "union":
        seen: dict[str, str] = {}
        for m in spec.members:
            for family in sorted(families(m.type)):
                if family in seen:
                    return f"a union of {seen[family]} and {m.type.text}"
                seen[family] = m.type.text
        inside = inside or "a union"
    elif spec.kind == "list":
        inside = inside or "a tuple[T, ...]"
    return next(
        (r for m in spec.members if (r := key_refusal(m.type, inside)) is not None),
        None,
    )


def collection_spec(
    hint: Any, where: str, capacity: int | None, depth: int, path: frozenset[int]
) -> TypeSpec:
    origin = get_origin(hint)
    if capacity is None:
        raise TypeError(
            f"{where}: {hint!r} needs a Capacity in elements, as in Annotated[{hint!r}, Capacity(n)]"
        )
    below = deeper(depth, where)
    bounded = bounded_class(hint)
    if bounded is not None:
        given = bounded_args(hint)
        if given is None:
            name = bounded.__name__
            example = "float" if issubclass(bounded, NativeCollection) else "int"
            raise TypeError(
                f"{where}: {name} needs its element type, as in Annotated[{name}[{example}], Capacity(n)]"
            )
        args: tuple[Any, ...] = given
    else:
        args = get_args(hint)
    if len(args) != (2 if origin in DICTS or origin is tuple else 1):
        raise unsupported(where, hint)
    if origin in DICTS:
        kind, read = "dict", DICTS[origin]
        key = parse(args[0], f"{where}[key]", depth=below, path=path)
        value = parse(args[1], f"{where}[value]", depth=below, path=path)
        if not key.hashable:
            raise TypeError(
                f"{where}: dict keys of {key.text} are not hashable when read back"
            )
        pair = max(key.alignment, value.alignment)
        at_value = round_up(key.size, value.alignment)
        stride = round_up(at_value + value.size, pair)
        members = (Member(0, key), Member(at_value, value))
        a = max(4, pair)
        text = f"dict[{capacity}]({key.text},{value.text})"
    else:
        if origin is tuple:
            kind, read = "list", tuple
        elif bounded is not None:
            kind = "list" if issubclass(bounded, NativeCollection) else "set"
            read = bounded
        elif origin in LISTS:
            kind, read = "list", LISTS[origin]
        else:
            kind, read = "set", SETS[origin]
        element = parse(args[0], f"{where}[]", depth=below, path=path)
        if kind == "set" and not element.hashable:
            raise TypeError(
                f"{where}: set elements of {element.text} are not hashable when read back"
            )
        if bounded is not None and kind == "set":
            reason = key_refusal(element)
            if reason is not None:
                raise TypeError(
                    f"{where}: {bounded.__name__} keys cannot hold {reason}"
                )
        stride = round_up(element.size, element.alignment)
        a = max(4, element.alignment)
        members = (Member(round_up(4, a), element),)  # type: ignore[assignment]
        text = f"{kind}[{capacity}]({element.text})"
    for m in members:
        if m.type.kind == "array":
            raise TypeError(
                f"{where}: an array can be a field or a record member, not a collection element"
            )
    slots = round_up(4, a)
    size = round_up(slots + capacity * stride, a)
    if size >= 1 << 32:
        raise TypeError(
            f"{where}: {capacity} elements take {size} bytes; a record holds less than 4 GiB"
        )
    return TypeSpec(
        kind,
        size,
        a,
        text,
        capacity=capacity,
        members=members,
        count=len(members),
        before=struct.pack("<I", capacity),
        info=(read, element.text, args[0]) if kind != "dict" else (read,),
        match=(
            (abc.Mapping,)
            if kind == "dict"
            else (abc.Set,)
            if kind == "set"
            else (abc.Sequence,)
        ),
        exact=read,
        hashable=read in (tuple, frozenset) and all(m.type.hashable for m in members),
        reusable=read in (tuple, frozenset) and all(m.type.reusable for m in members),
    )


class Table:
    """The description table of one class, written as its fields are laid out."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.info: dict[int, tuple[Any, ...]] = {}
        self.bytearrays: list[tuple[int, int]] = []
        self.converts: list[
            tuple[int, int, Callable[[Any], Any] | None, Callable[[Any], Any] | None]
        ] = []

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
        self.converts += [
            (at, i, m.type.read_as, m.type.write_as)
            for i, m in enumerate(spec.members)
            if m.type.read_as is not None or m.type.write_as is not None
        ]
        return at
