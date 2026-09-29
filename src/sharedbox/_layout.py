from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable, Mapping
from dataclasses import KW_ONLY, MISSING, InitVar, dataclass
from types import MappingProxyType
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

from ._native import check as native_check

Kind = Literal["bool", "int", "float", "str", "bytes"]

MAX_CAPACITY: Final[int] = 1 << 20
MAX_FIELDS: Final[int] = 256
ALIGN: Final[int] = 8
SCALARS: Final[dict[type, tuple[Kind, int]]] = {
    bool: ("bool", 1),
    int: ("int", 8),
    float: ("float", 8),
}
PREFIXED: Final[tuple[Kind, ...]] = ("str", "bytes")
KIND_CODES: Final[dict[Kind, int]] = {
    "bool": 0,
    "int": 1,
    "float": 2,
    "str": 3,
    "bytes": 4,
}
ALIGNMENT: Final[dict[Kind, int]] = {
    "int": 8,
    "float": 8,
    "str": 4,
    "bytes": 4,
    "bool": 1,
}


@dataclass(frozen=True)
class Capacity:
    """Maximum encoded size of a ``str`` or ``bytes`` field."""

    size: int
    """Bytes, not characters: UTF-8 text can need more than one per character."""

    def __post_init__(self) -> None:
        if not 0 < self.size <= MAX_CAPACITY:
            raise ValueError(
                f"capacity must be between 1 and {MAX_CAPACITY} bytes, got {self.size}"
            )


@dataclass(frozen=True)
class Field:
    """Options of one field of a ``SharedBox`` class, as :func:`field` takes them."""

    name: str
    type: Any
    """The annotation, with ``Annotated`` extras kept."""
    default: Any
    """``dataclasses.MISSING`` when there is none."""
    default_factory: Any
    """A function of no arguments, or ``dataclasses.MISSING``."""
    init: bool
    """False if the constructor takes no value for the field."""
    repr: bool
    """False if ``repr()`` of a box leaves the field out."""
    kw_only: Any
    """True if the constructor takes the value by keyword only; ``dataclasses.MISSING`` until the class is created."""
    metadata: Mapping[Any, Any]
    """Read-only; never stored in the segment."""
    doc: str | None


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
    """Options for one field of a ``SharedBox`` class, as ``dataclasses.field`` gives them.

    ``default_factory`` is called at each creation that is given no value
    for the field, and its result is stored like any other value.
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
    """Bytes reserved for the value, not counting the length prefix."""
    kind: int
    """0 bool, 1 int, 2 float, 3 str, 4 bytes."""


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
    label: str = ""
    """``"<Class>.<field>"``, used in error messages."""

    @property
    def native(self) -> NativeField:
        return NativeField(self.offset, self.capacity, KIND_CODES[self.kind])

    def check(self, value: Any) -> None:
        """Raise what writing ``value`` to this field would raise."""
        native_check(KIND_CODES[self.kind], self.capacity, self.label, value)


@dataclass(frozen=True)
class Layout:
    """Every field of a class, in declaration order, and the record they fill."""

    fields: tuple[FieldSpec, ...]
    """In declaration order, base classes first."""
    record_size: int
    """Bytes the record needs, a multiple of 8."""
    schema_hash: int
    """First 8 bytes of SHA-256 over the identity and every field's name, kind and capacity, read little-endian."""
    by_name: Mapping[str, FieldSpec]
    """Each field's spec under its name."""
    names: tuple[str, ...]
    """Field names in declaration order."""


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


def declared(cls: type) -> list[tuple[str, Any]]:
    """``(name, annotation)`` of every field and ``InitVar`` of ``cls``, base classes first.

    ``dataclasses.KW_ONLY`` and ``ClassVar`` annotations and names starting
    with ``_`` are left out.
    """
    return [
        (name, hint)
        for name, hint in get_type_hints(cls, include_extras=True).items()
        if hint is not KW_ONLY
        and not name.startswith("_")
        and hint is not ClassVar
        and get_origin(hint) is not ClassVar
    ]


def own_kw_only(cls: type, kw_only: bool = False) -> dict[str, bool]:
    """Keyword-only flag of each annotation ``cls`` itself declares, not those of its bases.

    An annotation is keyword-only when ``kw_only`` is true or it follows a
    ``dataclasses.KW_ONLY`` annotation of ``cls``.
    """
    hints = get_type_hints(cls, include_extras=True)
    flags: dict[str, bool] = {}
    for name in inspect.get_annotations(cls):
        if hints.get(name) is KW_ONLY:
            kw_only = True
            continue
        flags[name] = kw_only
    return flags


def build_layout(cls: type, identity: str | None = None) -> Layout:
    """Lay out the public annotated fields of ``cls``, base classes first.

    Fields are packed by descending alignment (8-byte fields, then
    ``str``/``bytes``, then ``bool``), not declaration order;
    ``Layout.fields`` keeps the declaration order. The schema hash covers
    ``identity``, by default :func:`class_identity`. ``InitVar``
    annotations are not fields.
    """
    found = [
        (name, *classify(name, hint))
        for name, hint in declared(cls)
        if not isinstance(hint, InitVar)
    ]
    if not found:
        raise TypeError(f"{cls.__qualname__} declares no fields")
    if len(found) > MAX_FIELDS:
        raise TypeError(
            f"{cls.__qualname__} declares {len(found)} fields; the limit is {MAX_FIELDS}"
        )
    order = sorted(range(len(found)), key=lambda i: -ALIGNMENT[found[i][1]])
    offsets = [0] * len(found)
    offset = 0
    for i in order:
        kind, capacity = found[i][1], found[i][2]
        align = ALIGNMENT[kind]
        offset = -(-offset // align) * align
        offsets[i] = offset
        offset += capacity + (4 if kind in PREFIXED else 0)
    record_size = -(-offset // ALIGN) * ALIGN
    specs = tuple(
        FieldSpec(
            name,
            i,
            kind,
            offsets[i],
            capacity,
            f"{cls.__qualname__}.{name}",
        )
        for i, (name, kind, capacity) in enumerate(found)
    )
    text = "|".join(
        [
            class_identity(cls) if identity is None else identity,
            *(f"{s.name}:{s.kind}:{s.capacity}" for s in specs),
        ]
    )
    schema_hash = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "little")
    return Layout(
        specs,
        record_size,
        schema_hash,
        {s.name: s for s in specs},
        tuple(s.name for s in specs),
    )
