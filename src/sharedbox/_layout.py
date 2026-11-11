from __future__ import annotations

import hashlib
import inspect
import re
import sys
import types
from collections.abc import Callable, Iterable, Mapping
from dataclasses import KW_ONLY, MISSING, InitVar, dataclass
from types import MappingProxyType, NoneType
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Final,
    ForwardRef,
    Literal,
    NamedTuple,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from ._arrays import Shape
from ._attr import Attr
from ._native import Types
from ._types import (
    CODES,
    MAX_ARRAY_PLACES,
    Table,
    TypeSpec,
    attr_spec,
    is_box_class,
    misplaced_max_array,
    parse,
    round_up,
)
from ._types import (
    Capacity as Capacity,  # noqa: PLC0414
)

if sys.version_info >= (3, 14):
    from annotationlib import Format

if TYPE_CHECKING:
    from ._box import SharedBox

Kind = Literal[
    "bool",
    "int",
    "float",
    "str",
    "bytes",
    "ref",
    "complex",
    "date",
    "time",
    "datetime",
    "timedelta",
    "uuid",
    "decimal",
    "int8",
    "int16",
    "int32",
    "uint8",
    "uint16",
    "uint32",
    "uint64",
    "float16",
    "float32",
    "complex64",
    "datetime64",
    "enum",
    "flag",
    "literal",
    "optional",
    "union",
    "record",
    "tuple",
    "list",
    "set",
    "dict",
    "array",
    "max_array",
    "attr",
    "timed",
]

MAX_FIELDS: Final[int] = 256
ALIGN: Final[int] = 8
REF_SIZE: Final[int] = 256


@dataclass(frozen=True, eq=False)
class Field:
    """Options of one field of a `SharedBox` class, as [`field`][sharedbox.field] takes them.

    [`fields`][sharedbox.fields] returns them. A `Field` is read-only, and
    two are equal only if they are the same object, as with
    `dataclasses.Field`.
    """

    name: str
    type: Any
    """The evaluated annotation, with `Annotated` extras kept."""
    default: Any
    """`dataclasses.MISSING` when there is none."""
    default_factory: Any
    """A function of no arguments, or `dataclasses.MISSING`."""
    init: bool
    """False if the constructor takes no value for the field."""
    repr: bool
    """False if `repr()` of a box leaves the field out."""
    kw_only: Any
    """True if the constructor takes the value by keyword only; `dataclasses.MISSING` until the class is created."""
    metadata: Mapping[Any, Any]
    """Read-only; kept in Python, never stored in the segment."""
    doc: str | None
    """Kept in Python, never stored in the segment."""
    shape: tuple[int, ...] | None = None
    """The `shape` option given to `field`, or None; a field sized by a `Shape` or a default array has None."""
    capacity: int | None = None
    """The `capacity` option given to `field`, or None; a field sized by a `Capacity` in its annotation has None."""
    is_attr: bool = False
    """True for a field declared with [`attr`][sharedbox.attr]."""
    timed: bool = False
    """The `timed` option given to `attr`."""
    settable: bool = False
    """The `settable` option given to `attr`."""


def field(
    *,
    default: Any = MISSING,
    default_factory: Callable[[], Any] | Any = MISSING,
    init: bool = True,
    repr: bool = True,
    kw_only: bool | Any = MISSING,
    metadata: Mapping[Any, Any] | None = None,
    doc: str | None = None,
    shape: Iterable[int] | None = None,
    capacity: int | None = None,
) -> Any:
    """Set the options of one field of a `SharedBox` class, as `dataclasses.field` does.

    A plain class attribute is still the field's default. There is no
    `compare` or `hash` option: two boxes are equal only if they are the
    same object. A field with neither `default` nor `default_factory` is
    required. A default is checked against the field's type and capacity
    when the class is defined, and a factory's result when a box is
    created; both raise what assigning the value raises, except that an array
    default of another shape than the field's sizes raises `TypeError` when
    the class is defined. Values are copied
    into the segment, and only the stored types exist: no lists, dicts or
    other objects.

    A positional field without a default cannot follow one with a default;
    a field with `default` or `default_factory` counts as having one, and
    `init=False` fields take no position and are not counted.

    Whether a field is keyword-only is fixed by the class that declares
    it, through its `kw_only` class keyword, a `dataclasses.KW_ONLY` among
    its own annotations, or `kw_only` here. A subclass keeps each
    inherited field's setting: its own `kw_only=True` affects only its own
    fields, and the fields of a `kw_only` base stay keyword-only in a
    subclass without the keyword.

    A subclass that declares an inherited field again, with an annotation,
    gives it default options, as in `dataclasses`. An annotation without a
    value keeps only a plain inherited `default`: the other options go
    back to their defaults, and a field whose base gave it a
    `default_factory` becomes required. The same holds for an `InitVar`
    declared over an inherited field.

    Parameters
    ----------
    default_factory
        Called once per creation that is given no value for the field. The
        result is stored like any other value, so the box never keeps the
        object the factory returned.
    init
        False leaves the field out of the constructor; the field then
        needs `default` or `default_factory`.
    repr
        False leaves the field out of `repr()` of a box.
    kw_only
        Make this field keyword-only or not, whatever the `kw_only` class
        keyword and `KW_ONLY` say.
    metadata
        Kept in Python as a read-only mapping, never stored in the
        segment.
    doc
        Kept in Python, never stored in the segment.
    shape
        Sizes of an array field, as `Shape` takes them. It must agree with
        a `Shape` in the annotation and with the shape of a plain
        `default` array. A subclass that declares the field again without
        `field(shape=...)` does not inherit it.
    capacity
        Capacity of a field of text, bytes, `bytearray`, `Decimal` or a
        collection, as [`Capacity`][sharedbox.Capacity] takes it, in place
        of a `Capacity` in the annotation. A subclass that declares the
        field again without `field(capacity=...)` does not inherit it.

    Raises
    ------
    ValueError
        If both `default` and `default_factory` are given, `shape` is
        not 1 to 8 sizes of at least 1, or `capacity` is not 1 to 1 Mi.
    TypeError
        When the class is defined, for `shape` on a field that is not an
        array, `capacity` on a field that takes none or beside a
        `Capacity` in the annotation, for `field()` on a name that is not a
        field (a name starting with `_`, a `ClassVar`), `field()` without
        an annotation, `init=False` without a default, or a subclass that
        sets a plain class attribute, without an annotation, on the name
        of an inherited field.
    """
    if default is not MISSING and default_factory is not MISSING:
        raise ValueError("cannot specify both default and default_factory")
    return Field(
        name="",
        type=None,
        default=default,
        default_factory=default_factory,
        init=init,
        repr=repr,
        kw_only=kw_only,
        metadata=MappingProxyType(dict(metadata or {})),
        doc=doc,
        shape=None if shape is None else Shape(tuple(shape)).dims,
        capacity=None if capacity is None else Capacity(capacity).size,
    )


def attr(
    *,
    init: Literal[False] = False,
    timed: bool = False,
    settable: bool = False,
    default: Any = MISSING,
    metadata: Mapping[Any, Any] | None = None,
    doc: str | None = None,
) -> Any:
    """Declare an [`Attr`][sharedbox.Attr] field of a `SharedBox` class.

    Write `exposure: Attr[float] = attr(timed=True)`. An attribute is
    published by its owner, so the box's constructor takes no value for it,
    for type checkers too.

    Parameters
    ----------
    init
        Leave it out. Type checkers read its default.
    timed
        Store a timestamp with the value.
    settable
        Give the attribute a setpoint of the same type as the value.
    default
        The starting value, and the starting setpoint. Without it the
        value starts at the zero of its type, which a record has none of.
    metadata, doc
        As [`field`][sharedbox.field] takes them.
    """
    return Field(
        name="",
        type=None,
        default=default,
        default_factory=MISSING,
        init=init,
        repr=True,
        kw_only=MISSING,
        metadata=MappingProxyType(dict(metadata or {})),
        doc=doc,
        is_attr=True,
        timed=bool(timed),
        settable=bool(settable),
    )


class NativeField(NamedTuple):
    """A field's place in the record, in the form the native segment takes."""

    offset: int
    """Byte offset of the field from the start of the record."""
    capacity: int
    """Bytes reserved for the value, not counting a length prefix; for a described kind, the offset of its description."""
    kind: int
    """The kind code: 0 to 12 for fixed kinds, 64 and up for described ones."""


@dataclass(frozen=True)
class AttrInfo:
    """The parts of an `Attr` field."""

    has_value: bool
    """False for an attribute whose type is `None`."""
    timed: bool
    settable: bool
    request: bool
    default: Any = MISSING
    """`dataclasses.MISSING` when the value starts at zero."""

    @property
    def initial(self) -> dict[str, Any]:
        """The starting parts the field is created with: its default as the value, and as the setpoint if settable."""
        if self.default is MISSING:
            return {}
        if self.settable:
            return {"value": self.default, "setpoint": self.default}
        return {"value": self.default}


@dataclass(frozen=True)
class FieldSpec:
    """Place and encoding of one field in a shared record."""

    name: str
    index: int
    """Position in declaration order; also the field's number in the native segment."""
    kind: Kind
    """The key of the field's kind in the kind table, such as `"int"` or `"datetime"`."""
    offset: int
    """Byte offset of the field from the start of the record."""
    capacity: int
    """Bytes the value takes in the record.

    For `str`, `bytes` and `Decimal` it is the most the value may take,
    without its 4-byte length prefix. For a described kind,
    [`native`][sharedbox._layout.FieldSpec.native] gives the native module
    the offset of the field's description in place of this size.
    """
    label: str = ""
    """`"<Class>.<field>"`, used in error messages."""
    target: type[SharedBox] | None = None
    """The box class a reference field is annotated with; None for every other kind."""
    optional: bool = False
    """True for a reference field annotated `X | None`, which may be empty."""
    type: TypeSpec | None = None
    """How the value is stored; None for a reference field."""
    description: int = -1
    """Offset of the field's description in the class's table; -1 for a kind without one."""
    attr: AttrInfo | None = None
    """The parts of an `Attr` field; None for every other kind."""

    @property
    def native(self) -> NativeField:
        low = self.description if self.description >= 0 else self.capacity
        return NativeField(self.offset, low, CODES[self.kind])


@dataclass(frozen=True)
class Layout:
    """Every field of a class, in declaration order, and the record they fill."""

    fields: tuple[FieldSpec, ...]
    """In declaration order, base classes first."""
    record_size: int
    """Bytes the record needs, a multiple of 8."""
    schema_hash: int
    """First 8 bytes, read little-endian, of SHA-256 over the identity and every field's name and type.

    The type covers the kind and capacity, and for a described kind its
    description: the member names of an enum, the names and values of a
    flag, the values of a literal, the members of a union, record or
    tuple, the element types of a collection, and the shape and dtype of
    an array. For a reference field it covers whether the field is optional
    and the identity of the class it points to.
    """
    by_name: Mapping[str, FieldSpec]
    """Each field's spec under its name."""
    update_names: tuple[str | None, ...]
    """Field names in declaration order; None for an attr field, which `update` does not write."""
    snapshot_names: tuple[str | None, ...]
    """Field names in declaration order; None for an attr whose type is `None`, which a snapshot leaves out."""
    refs: tuple[FieldSpec, ...]
    """The reference fields, in declaration order."""
    values: tuple[FieldSpec, ...]
    """The fields a new box is given a starting value for, in declaration order: every field but an attr without a default."""
    watched: tuple[FieldSpec, ...]
    """The fields `watch` and `events` report, in declaration order: every field but an attr whose type is `None`."""
    types: Types
    """The field types the native module converts values with."""

    def check(self, spec: FieldSpec, value: Any) -> None:
        """Raise what writing `value` to the field `spec` would raise."""
        self.types.check(spec.index, value)


def class_identity(cls: type) -> str:
    """Return `module.qualname` of `cls`, the same in a spawned child as in its parent."""
    # multiprocessing's spawn start method re-imports the main script as __mp_main__.
    module = "__main__" if cls.__module__ == "__mp_main__" else cls.__module__
    return f"{module}.{cls.__qualname__}"


def reference(hint: object) -> tuple[type[SharedBox], bool] | None:
    """Return the box class of a reference field annotated `X` or `X | None`, and whether it may be None."""
    if is_box_class(hint):
        return hint, False
    if get_origin(hint) in (Union, types.UnionType):
        args = get_args(hint)
        boxes = [arg for arg in args if is_box_class(arg)]
        if len(args) == 2 and type(None) in args and len(boxes) == 1:
            return boxes[0], True
    return None


def undefined(cls: type, field: str | None, name: str) -> str:
    where = cls.__qualname__ if field is None else f"{cls.__qualname__}.{field}"
    return (
        f"{where}: {name!r} is not defined; a class named in a field annotation, "
        "such as a box class a field refers to, must be defined first"
    )


def forward_refs(hint: object) -> list[ForwardRef]:
    """Return every `ForwardRef` in `hint`, at any depth."""
    if isinstance(hint, ForwardRef):
        return [hint]
    return [ref for arg in get_args(hint) for ref in forward_refs(arg)]


def enclosing_locals() -> dict[str, Any]:
    """Return the local names of the function whose class statement is being run, or {} at module level.

    With string annotations a class defined in a function can only name
    another class of that function through these.
    """
    package = __name__.partition(".")[0]
    frame: types.FrameType | None = sys._getframe(1)
    while frame is not None and (
        frame.f_globals.get("__name__", "").partition(".")[0] == package
    ):
        frame = frame.f_back
    if frame is None or frame.f_locals is frame.f_globals:
        return {}
    return dict(frame.f_locals)


def type_hints(cls: type) -> dict[str, Any]:
    """Return the resolved annotations of `cls` and its bases, in which `cls` may name itself.

    Raises
    ------
    TypeError
        If an annotation names something that is not defined.
    """
    try:
        return get_type_hints(cls, include_extras=True)
    except NameError:
        pass
    # The class's own name is bound in its module only after __init_subclass__ returns.
    localns = {**enclosing_locals(), cls.__name__: cls}
    if sys.version_info >= (3, 14):
        hints = get_type_hints(
            cls, include_extras=True, localns=localns, format=Format.FORWARDREF
        )
        for field, hint in hints.items():
            for ref in forward_refs(hint):
                # A ForwardRef can hold a whole expression, such as "Later | None";
                # evaluating it names the part that is undefined.
                try:
                    ref.evaluate(locals=localns)
                except NameError as error:
                    missing = error.name or ref.__forward_arg__
                    raise TypeError(undefined(cls, field, missing)) from None
        return hints
    try:
        return get_type_hints(cls, include_extras=True, localns=localns)
    except NameError as error:
        missing = error.name or str(error)
        own = vars(cls).get("__annotations__", {})
        field = next(
            (
                field
                for field, hint in own.items()
                if missing
                in re.findall(
                    r"\w+",
                    hint
                    if isinstance(hint, str)
                    else " ".join(ref.__forward_arg__ for ref in forward_refs(hint)),
                )
            ),
            None,
        )
        raise TypeError(undefined(cls, field, missing)) from None


def own_annotations(cls: type) -> dict[str, Any]:
    """Return the annotations `cls` itself declares, in declaration order, without failing on a name not bound yet."""
    if sys.version_info >= (3, 14):
        # Evaluating would fail for a class that names itself, which is not bound yet.
        return inspect.get_annotations(cls, format=Format.FORWARDREF)
    return inspect.get_annotations(cls)


def declared(cls: type) -> list[tuple[str, Any]]:
    """Return `(name, annotation)` of every field and `InitVar` of `cls`, base classes first.

    `dataclasses.KW_ONLY` and `ClassVar` annotations and names starting
    with `_` are left out.
    """
    return [
        (name, hint)
        for name, hint in type_hints(cls).items()
        if hint is not KW_ONLY
        and not name.startswith("_")
        and hint is not ClassVar
        and get_origin(hint) is not ClassVar
    ]


def own_kw_only(cls: type, kw_only: bool = False) -> dict[str, bool]:
    """Return the keyword-only flag of each annotation `cls` itself declares, not those of its bases.

    An annotation is keyword-only when `kw_only` is true or it follows a
    `dataclasses.KW_ONLY` annotation of `cls`.
    """
    hints = type_hints(cls)
    flags: dict[str, bool] = {}
    for name in own_annotations(cls):
        if hints.get(name) is KW_ONLY:
            kw_only = True
            continue
        flags[name] = kw_only
    return flags


def field_text(spec: FieldSpec) -> str:
    """Return the part of the schema text that covers `spec`."""
    if spec.type is not None:
        return f"{spec.name}:{spec.type.text}"
    assert spec.target is not None
    optional = "?" if spec.optional else ""
    return f"{spec.name}:ref{optional}:{spec.target.__sharedbox_identity__}"


def sizing_options(
    cls: type, name: str
) -> tuple[tuple[int, ...] | None, Any, int | None]:
    """Return the `field(shape=...)`, the plain default and the `field(capacity=...)` `cls` gives field `name`, following the rules of inherited options."""
    own = name in own_annotations(cls)
    value = cls.__dict__.get(name, MISSING) if own else MISSING
    if value is MISSING:
        option = next(
            (
                base.__dict__["__sharedbox_options__"][name]
                for base in cls.__mro__[1:]
                if name in base.__dict__.get("__sharedbox_options__", {})
            ),
            None,
        )
        if option is None:
            return None, MISSING, None
        if own:
            return None, option.default, None
        return option.shape, option.default, option.capacity
    if isinstance(value, Field):
        return value.shape, value.default, value.capacity
    return None, value, None


def declared_option(cls: type, name: str) -> Field | None:
    """Return the `Field` that `field` or `attr` gave `name` in `cls`, or the one it inherits; None for a plain default or none."""
    if name in own_annotations(cls):
        value = cls.__dict__.get(name, MISSING)
        return value if isinstance(value, Field) else None
    return next(
        (
            base.__dict__["__sharedbox_options__"][name]
            for base in cls.__mro__[1:]
            if name in base.__dict__.get("__sharedbox_options__", {})
        ),
        None,
    )


ZERO_KINDS: Final = frozenset(
    {
        "bool",
        "int",
        "float",
        "complex",
        "str",
        "bytes",
        "int8",
        "int16",
        "int32",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "float16",
        "float32",
        "complex64",
        "datetime64",
        "array",
        "max_array",
    }
)


def attr_field(
    hint: object, where: str, option: Field | None, default: Any
) -> tuple[TypeSpec, AttrInfo]:
    """Return how an `Attr[T, P]` field declared with `option` is stored.

    Raises
    ------
    TypeError
        If the field is not declared with `attr()`, `T` or an argument cannot
        be stored in an attribute, `timed` or `settable` is given for a type of
        `None`, `settable` for arguments, or the value has no zero and no default.
    """
    if option is None or not option.is_attr:
        raise TypeError(
            f"{where}: an Attr field is declared with attr(), as in x: Attr[float] = attr()"
        )
    if option.init:
        raise TypeError(
            f"{where}: attr() takes init=False, since an Attr takes no constructor value"
        )
    args = get_args(hint)
    if not args:
        raise TypeError(f"{where}: Attr needs its value type, as Attr[float]")
    value = args[0]
    arguments = args[1] if len(args) > 1 else ()
    if not isinstance(arguments, (tuple, list)):
        raise TypeError(
            f"{where}: Attr takes a list of argument types, as Attr[float, [float, str]]"
        )
    has_value = value is not None and value is not NoneType
    if not has_value and (option.timed or option.settable):
        raise TypeError(f"{where}: an Attr[None] has no value to be timed or settable")
    if option.settable and arguments:
        raise TypeError(
            f"{where}: a settable Attr takes its value as the one argument of its request, so it has no argument types"
        )
    if not has_value and default is not MISSING:
        raise TypeError(f"{where}: an Attr[None] has no value to give a default")
    arguments = (value,) if option.settable else tuple(arguments)
    request = bool(arguments) or not has_value
    spec = attr_spec(
        value,
        arguments,
        where,
        frozenset(),
        timed=option.timed,
        settable=option.settable,
        request=request,
    )
    if has_value and default is MISSING and spec.members[0].type.kind not in ZERO_KINDS:
        raise TypeError(
            f"{where}: {spec.members[0].type.kind} has no zero value, so the Attr needs attr(default=...)"
        )
    return spec, AttrInfo(has_value, option.timed, option.settable, request, default)


def build_layout(cls: type, identity: str | None = None) -> Layout:
    """Lay out the public annotated fields of `cls`, base classes first.

    Fields are packed by descending alignment, not declaration order;
    `Layout.fields` keeps the declaration order. `InitVar` annotations are
    not fields. A field annotated with a `SharedBox` subclass `X`, or
    `X | None`, refers to a box of `X`.

    Parameters
    ----------
    identity
        The identity the schema hash covers; by default
        [`class_identity`][sharedbox._layout.class_identity] of `cls`.

    Raises
    ------
    TypeError
        If `cls` declares no fields, more than 256, a field with an
        unsupported annotation, or one naming a class that is not defined.
    """
    found: list[
        tuple[
            str,
            TypeSpec | None,
            tuple[type[SharedBox], bool] | None,
            AttrInfo | None,
        ]
    ] = []
    for name, hint in declared(cls):
        if isinstance(hint, InitVar):
            continue
        ref = reference(hint)
        shape, default, capacity = sizing_options(cls, name)
        where = f"{cls.__qualname__}.{name}"
        option = declared_option(cls, name)
        is_attr_hint = hint is Attr or get_origin(hint) is Attr
        info: AttrInfo | None = None
        if option is not None and option.is_attr and not is_attr_hint:
            raise TypeError(f"{where}: attr() applies only to a field annotated Attr")
        if ref is not None and shape is not None:
            raise TypeError(f"{where}: field(shape=...) applies only to an array field")
        if ref is not None and capacity is not None:
            raise TypeError(f"{where}: Capacity does not apply to a reference")
        if is_attr_hint:
            if shape is not None or capacity is not None:
                raise TypeError(
                    f"{where}: field(shape=...) and field(capacity=...) do not apply to an Attr"
                )
            spec: TypeSpec | None
            spec, info = attr_field(hint, where, option, default)
        elif ref is not None:
            spec = None
        else:
            spec = parse(
                hint,
                where,
                capacity=capacity,
                sizes=(
                    None if shape is None else (f"field(shape={shape})", shape),
                    default,
                ),
            )
        if spec is not None and misplaced_max_array(spec):
            raise TypeError(f"{where}: {MAX_ARRAY_PLACES}")
        found.append((name, spec, ref, info))
    if not found:
        raise TypeError(f"{cls.__qualname__} declares no fields")
    if len(found) > MAX_FIELDS:
        raise TypeError(
            f"{cls.__qualname__} declares {len(found)} fields; the limit is {MAX_FIELDS}"
        )
    aligns = [8 if spec is None else spec.alignment for _, spec, _, _ in found]
    sizes = [REF_SIZE if spec is None else spec.size for _, spec, _, _ in found]
    offsets = [0] * len(found)
    offset = 0
    for i in sorted(range(len(found)), key=lambda i: -aligns[i]):
        offset = round_up(offset, aligns[i])
        offsets[i] = offset
        offset += sizes[i]
    record_size = round_up(offset, max(ALIGN, *aligns))
    table = Table()
    specs: list[FieldSpec] = []
    for i, (name, spec, ref, info) in enumerate(found):
        if spec is None:
            assert ref is not None
            specs.append(
                FieldSpec(
                    name,
                    i,
                    "ref",
                    offsets[i],
                    REF_SIZE,
                    f"{cls.__qualname__}.{name}",
                    *ref,
                )
            )
            continue
        description = table.describe(spec) if spec.described else -1
        if spec.bytearray:
            table.bytearrays.append((-1, i))
        if spec.read_as is not None or spec.write_as is not None:
            table.converts.append((-1, i, spec.read_as, spec.write_as))
        specs.append(
            FieldSpec(
                name,
                i,
                cast(Kind, spec.kind),
                offsets[i],
                spec.entry_low,
                f"{cls.__qualname__}.{name}",
                type=spec,
                description=description,
                attr=info,
            )
        )
    text = "|".join(
        [
            class_identity(cls) if identity is None else identity,
            *map(field_text, specs),
        ]
    )
    schema_hash = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "little")
    types = Types(
        [s.native for s in specs],
        [s.label for s in specs],
        bytes(table.data),
        table.info,
        table.bytearrays,
        # A value of kind 0 to 5 costs no more to build than the check that would reuse it, unless a
        # conversion follows.
        [
            s.index
            for s in specs
            if s.type is not None
            and s.type.reusable
            and (s.type.code > CODES["ref"] or s.type.read_as is not None)
        ],
        table.converts,
    )
    watched = tuple(s for s in specs if s.attr is None or s.attr.has_value)
    return Layout(
        tuple(specs),
        record_size,
        schema_hash,
        {s.name: s for s in specs},
        tuple(None if s.attr is not None else s.name for s in specs),
        tuple(
            None if s.attr is not None and not s.attr.has_value else s.name
            for s in specs
        ),
        tuple(s for s in specs if s.target is not None),
        tuple(s for s in specs if s.attr is None or s.attr.default is not MISSING),
        watched,
        types,
    )
