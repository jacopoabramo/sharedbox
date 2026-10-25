"""BoundedList, a list value that keeps the stored form of a list field."""

from __future__ import annotations

import functools
import types
from collections.abc import Callable, Iterable, MutableSequence
from typing import Annotated, Any, Self, TypeVar, cast

from ._layout import Capacity
from ._native import NativeCollection, Types
from ._stream import item_of
from ._types import list_element

T = TypeVar("T")
C = TypeVar("C", bound=NativeCollection[Any])


class BoundedList(NativeCollection[T]):
    """A list of at most a field's capacity of elements, held as the field stores them.

    Declared as `Annotated[BoundedList[T], Capacity(n)]` or with
    `field(capacity=n)`, it is stored as `list[T]` is, and a read returns
    one. Indexing decodes or encodes one element, and setting an element
    checks the value as a write to the field would. A change that would
    leave more elements than the capacity raises `ValueError` and leaves
    the value as it was.

    `BoundedList[T](values, capacity=n)` builds one outside a box. Copies,
    slices, `+` and `*` keep the class, the element type and the capacity,
    and a pickled value is rebuilt from them and its bytes.

    A subclass is declared and read back the same way, generic as
    `class MyList(BoundedList[T])` used as `MyList[float]`, or with its
    element type fixed as `class Points(BoundedList[float])`. A read from a
    box calls neither `__new__` nor `__init__`.
    """

    __slots__ = ()

    def __new__(cls, values: Iterable[T] = (), *, capacity: int) -> Self:
        """Build a value of `capacity` holding `values`, of the element type `cls` fixes.

        Raises
        ------
        TypeError
            If `cls` fixes no element type, as `BoundedList` alone does, or
            an element is not of that type.
        ValueError
            If an element does not fit, or there are more than `capacity`.
        """
        return _build(cls, list_element(cls), values, capacity)

    def __init__(self, values: Iterable[T] = (), *, capacity: int) -> None:
        """Do nothing; `__new__` has built the value."""

    def __class_getitem__(cls, element: object) -> types.GenericAlias:
        return _Alias(cls, element)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}[{_name(self.element_annotation)}]"
            f"({list(self)!r}, capacity={self.capacity})"
        )

    def __reduce__(
        self,
    ) -> tuple[
        Callable[[type[Any], object, int, bytes], BoundedList[Any]],
        tuple[type[Any], object, int, bytes],
        dict[str, Any] | None,
    ]:
        return (
            _rebuild,
            (type(self), self.element_annotation, self.capacity, self._stored()),
            getattr(self, "__dict__", None),
        )


class _Alias(types.GenericAlias):
    """`BoundedList[T]` or a subclass's, which builds a value of element type `T` when called."""

    def __call__(
        self, values: Iterable[object] = (), *, capacity: int
    ) -> BoundedList[Any]:
        cls = cast("type[BoundedList[Any]]", self.__origin__)
        return _build(cls, list_element(self), values, capacity)


def _name(element: object) -> str:
    return element.__qualname__ if isinstance(element, type) else repr(element)


def _build(cls: type[C], element: object, values: Iterable[Any], capacity: int) -> C:
    """Return a new `cls` of `capacity` holding `values`, or raise `TypeError` when `element` is None."""
    if element is None:
        raise TypeError(
            f"{cls.__name__} needs its element type, as in"
            f" {cls.__name__}[float](values, capacity=n)"
        )
    codec, empty = _codec(cls, element, capacity)
    value = cast("C", codec.decode(0, bytes(empty)))
    value.extend(values)
    return value


def _new_codec(cls: type, element: object, capacity: int) -> tuple[Types, int]:
    """Return a one-field codec of `cls` holding `element` and the size of its empty stored form."""
    item = item_of(
        Annotated[_Alias(cls, element), Capacity(capacity)],
        label=f"{cls.__name__}[{_name(element)}]",
    )
    return item.types, item.spec.members[0].offset


_cached_codec = functools.lru_cache(_new_codec)


def _codec(cls: type, element: object, capacity: int) -> tuple[Types, int]:
    """Return `_new_codec(cls, element, capacity)`, cached when `element` can be hashed."""
    try:
        hash(element)
    except TypeError:
        return _new_codec(cls, element, capacity)
    return _cached_codec(cls, element, capacity)


def _rebuild(
    cls: type[Any], element: object, capacity: int, stored: bytes
) -> BoundedList[Any]:
    """Return the `cls` whose stored form is `stored`, as pickling gave it."""
    return cast("BoundedList[Any]", _codec(cls, element, capacity)[0].decode(0, stored))


MutableSequence.register(NativeCollection)
