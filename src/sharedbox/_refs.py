from __future__ import annotations

import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self, overload

from ._native import SchemaMismatchError, SegmentNotFoundError

if TYPE_CHECKING:
    from ._box import SharedBox
    from ._layout import FieldSpec

# Weak, so that a class nothing else uses, such as one defined inside a function, can be freed.
CLASSES: dict[int, list[weakref.ref[type[SharedBox]]]] = {}


def register(cls: type[SharedBox]) -> None:
    """Record `cls` under its schema hash, after any class already recorded there."""
    classes = CLASSES.setdefault(cls.__layout__.schema_hash, [])
    classes.append(weakref.ref(cls, classes.remove))


def box_class(schema_hash: int) -> type[SharedBox] | None:
    """The first class defined with `schema_hash` that is still alive, or None if there is none."""
    # A copy, since a class freed during the loop removes its entry from the list.
    for ref in tuple(CLASSES.get(schema_hash, ())):
        cls = ref()
        if cls is not None:
            return cls
    return None


class BrokenReferenceError(LookupError):
    """The box a reference field refers to no longer exists, or was created again."""


class UnknownBoxClassError(TypeError):
    """No class defined in this process has the schema hash of the box a reference field refers to."""


@dataclass(frozen=True)
class BoxRef:
    """The box a reference field refers to, as [`snapshot`][sharedbox.SharedBox.snapshot], [`events`][sharedbox.SharedBox.events] and [`watch`][sharedbox.SharedBox.watch] report it."""

    name: str
    """The box's name, which [`attach`][sharedbox.SharedBox.attach] takes."""
    schema_hash: int
    """Schema hash of the box's own class, which may be a subclass of the annotated one."""
    create_id: int
    """Tells the box apart from one created later under the same name."""

    @property
    def box_class(self) -> type[SharedBox] | None:
        """The class defined in this process with `schema_hash`, or None if there is none."""
        return box_class(self.schema_hash)


def box_ref(value: Any) -> BoxRef | None:
    """A reference field's native value, `(create_id, schema_hash, name)` or None, as callers see it."""
    return None if value is None else BoxRef(value[2], value[1], value[0])


def shown(spec: FieldSpec, value: Any) -> Any:
    """A field's native value as callers see it: a reference field gives a [`BoxRef`][sharedbox.BoxRef] or None."""
    return value if spec.target is None else box_ref(value)


def stored(spec: FieldSpec, value: Any) -> tuple[int, int, str] | None:
    """What a reference field stores for `value`: None, or the box's create id, schema hash and name.

    Raises
    ------
    TypeError
        If `value` is None and the field does not allow it, or is not a
        box of the field's class, of a subclass, or of a class with the
        same schema hash.
    BoxClosedError
        If `value` is a closed box.
    """
    target = spec.target
    assert target is not None
    if value is None:
        if spec.optional:
            return None
        raise TypeError(
            f"{spec.label} refers to a {target.__qualname__} box and cannot be None; "
            f"annotate it {target.__qualname__} | None to allow None"
        )
    layout = getattr(type(value), "__layout__", None)
    if layout is None or not (
        isinstance(value, target) or layout.schema_hash == target.__layout__.schema_hash
    ):
        raise TypeError(
            f"{spec.label} expects a {target.__qualname__} box, one of a subclass, "
            f"or None, got {type(value).__qualname__}"
        )
    return (value._segment.create_id, layout.schema_hash, value.name)


def attach_reference(
    spec: FieldSpec, create_id: int, schema_hash: int, name: str
) -> SharedBox:
    """Attach the box called `name`, which must still be the one with `create_id`.

    Raises
    ------
    UnknownBoxClassError
        If no class defined in this process has `schema_hash`.
    BrokenReferenceError
        If `name` is not a valid box name, no box has that name, or the box
        under it was created after the reference was stored.
    """
    from ._box import NAME

    where = f"{spec.label} refers to box {name!r}, which"
    if not NAME.fullmatch(name):
        raise BrokenReferenceError(f"{where} is not a valid box name")
    cls = box_class(schema_hash)
    if cls is None:
        raise UnknownBoxClassError(
            f"{spec.label} refers to box {name!r}, whose class (schema hash "
            f"{schema_hash:#018x}) is not defined in this process; import the module "
            "that defines it"
        )
    try:
        box = cls.attach(name)
    except SegmentNotFoundError:
        raise BrokenReferenceError(f"{where} no longer exists") from None
    except SchemaMismatchError:
        raise BrokenReferenceError(
            f"{where} was created again after it was assigned"
        ) from None
    if box._segment.create_id != create_id:
        box.close()
        raise BrokenReferenceError(f"{where} was created again after it was assigned")
    return box


class Reference:
    """Reads and writes a reference field: the box it refers to, or None when it is empty."""

    __slots__ = ("index", "spec")

    def __init__(self, spec: FieldSpec) -> None:
        self.spec = spec
        self.index = spec.index

    @overload
    def __get__(self, box: None, owner: type | None = None) -> Self: ...
    @overload
    def __get__(
        self, box: SharedBox, owner: type | None = None
    ) -> SharedBox | None: ...
    def __get__(
        self, box: SharedBox | None, owner: type | None = None
    ) -> Self | SharedBox | None:
        if box is None:
            return self
        # The usual read: the box this handle attached before, found by create id alone.
        hit = box._segment.cached_ref(self.index, box._refs)
        if hit is not False:
            return hit  # type: ignore[return-value]
        value: Any = box._segment.get(self.index)
        if value is None:
            return None
        return box._inner(self.spec, *value)

    def __set__(self, box: SharedBox, value: object) -> None:
        box._segment.set([(self.index, stored(self.spec, value))])

    def __delete__(self, box: SharedBox) -> None:
        raise AttributeError("a SharedBox field cannot be deleted")
