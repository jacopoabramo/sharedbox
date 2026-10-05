from __future__ import annotations

import hashlib
import inspect
import re
import sys
import types
from collections.abc import Callable, Mapping
from dataclasses import KW_ONLY, MISSING, InitVar, dataclass
from types import MappingProxyType
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

from ._native import Types
from ._types import (
    CODES,
    Table,
    TypeSpec,
    is_box_class,
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
]

MAX_FIELDS: Final[int] = 256
ALIGN: Final[int] = 8
REF_SIZE: Final[int] = 144


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


def field(
    *,
    default: Any = MISSING,
    default_factory: Callable[[], Any] | Any = MISSING,
    init: bool = True,
    repr: bool = True,
    kw_only: bool | Any = MISSING,
    metadata: Mapping[Any, Any] | None = None,
    doc: str | None = None,
) -> Any:
    """Set the options of one field of a `SharedBox` class, as `dataclasses.field` does.

    A plain class attribute is still the field's default. There is no
    `compare` or `hash` option: two boxes are equal only if they are the
    same object. A field with neither `default` nor `default_factory` is
    required. A default is checked against the field's type and capacity
    when the class is defined, and a factory's result when a box is
    created; both raise what assigning the value raises. Values are copied
    into the segment, and only the stored types exist: no lists, dicts or
    other objects. [`Capacity`][sharedbox.Capacity] goes in the
    annotation, not here.

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

    Raises
    ------
    ValueError
        If both `default` and `default_factory` are given.
    TypeError
        When the class is defined, for `field()` on a name that is not a
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
    names: tuple[str, ...]
    """Field names in declaration order."""
    refs: tuple[FieldSpec, ...]
    """The reference fields, in declaration order."""
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
    localns = {cls.__name__: cls}
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
    found: list[tuple[str, TypeSpec | None, tuple[type[SharedBox], bool] | None]] = []
    for name, hint in declared(cls):
        if isinstance(hint, InitVar):
            continue
        ref = reference(hint)
        spec = None if ref is not None else parse(hint, f"{cls.__qualname__}.{name}")
        found.append((name, spec, ref))
    if not found:
        raise TypeError(f"{cls.__qualname__} declares no fields")
    if len(found) > MAX_FIELDS:
        raise TypeError(
            f"{cls.__qualname__} declares {len(found)} fields; the limit is {MAX_FIELDS}"
        )
    aligns = [8 if spec is None else spec.alignment for _, spec, _ in found]
    sizes = [REF_SIZE if spec is None else spec.size for _, spec, _ in found]
    offsets = [0] * len(found)
    offset = 0
    for i in sorted(range(len(found)), key=lambda i: -aligns[i]):
        offset = round_up(offset, aligns[i])
        offsets[i] = offset
        offset += sizes[i]
    record_size = round_up(offset, max(ALIGN, *aligns))
    table = Table()
    specs: list[FieldSpec] = []
    for i, (name, spec, ref) in enumerate(found):
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
    )
    return Layout(
        tuple(specs),
        record_size,
        schema_hash,
        {s.name: s for s in specs},
        tuple(s.name for s in specs),
        tuple(s for s in specs if s.target is not None),
        types,
    )
