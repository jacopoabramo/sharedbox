"""BoundedList, a list value that keeps the stored form of a list field."""

from __future__ import annotations

import copy
import functools
import types
from collections.abc import Callable, Iterable
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
    box calls neither `__new__` nor `__init__`. `copy.copy`,
    `copy.deepcopy` and pickling keep a subclass's attributes, as for a
    `list` subclass, while slices, `+` and `*` return the class without
    them.
    """

    __slots__ = ()

    def __new__(
        cls,
        values: Iterable[T] = (),
        *args: Any,
        capacity: int,
        **kwargs: Any,
    ) -> Self:
        """Build a value of `capacity` holding `values`, of the element type `cls` fixes.

        Other arguments are left to `__init__`, as `list.__new__` leaves
        them, so a subclass's `__init__` may take more. It receives the same
        `values` object after `__new__` has consumed it, so a generator
        arrives empty.

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
        """Return `cls` with its element type `element`.

        Raises
        ------
        TypeError
            If `element` is several types, or `cls` already fixes one.
        """
        # typing rebuilds an alias it evaluates with its arguments as a tuple.
        if isinstance(element, tuple):
            if len(element) != 1:
                raise TypeError(
                    f"{cls.__name__} takes one element type, got {len(element)}"
                )
            (element,) = element
        fixed = list_element(cls)
        if fixed is not None:
            raise TypeError(f"{cls.__name__} already has element type {_name(fixed)}")
        return _Alias(cls, element)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}[{_name(self._element_annotation)}]"
            f"({list(self)!r}, capacity={self.capacity})"
        )

    def __copy__(self) -> Self:
        """Return a copy, with a shallow copy of a subclass's attributes."""
        value = self.copy()
        _restore(value, self.__getstate__())
        return value

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        """Return a copy, with a deep copy of a subclass's attributes.

        The elements are copied as bytes, since every read decodes an
        element anew and the copy shares none with this value.
        """
        value = self.copy()
        memo[id(self)] = value
        _restore(value, copy.deepcopy(self.__getstate__(), memo))
        return value

    def __reduce__(
        self,
    ) -> tuple[
        Callable[[type[Any], object, int, bytes], BoundedList[Any]],
        tuple[type[Any], object, int, bytes],
        object,
    ]:
        return (
            _rebuild,
            (type(self), self._element_annotation, self.capacity, self._stored()),
            self.__getstate__(),
        )


class _Alias(types.GenericAlias):
    """`BoundedList[T]` or a subclass's, which builds a value of element type `T` when called."""

    def __call__(
        self,
        values: Iterable[object] = (),
        *args: Any,
        capacity: int,
        **kwargs: Any,
    ) -> BoundedList[Any]:
        cls = cast("type[BoundedList[Any]]", self.__origin__)
        value = _build(cls, list_element(self), values, capacity)
        cls.__init__(value, values, *args, capacity=capacity, **kwargs)
        return value


def _restore(value: object, state: object) -> None:
    """Set the attributes `object.__getstate__` gave on `value`, as unpickling sets them."""
    attrs, slots = state if isinstance(state, tuple) else (state, None)
    if attrs:
        value.__dict__.update(attrs)
    for name, member in (slots or {}).items():
        setattr(value, name, member)


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
