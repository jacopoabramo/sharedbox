"""BoundedList, a list value that keeps the stored form of a list field."""

from __future__ import annotations

import functools
import types
from collections.abc import Callable, Iterable, MutableSequence
from typing import Annotated, Any, TypeVar, cast

from ._layout import Capacity
from ._native import NativeCollection, Types
from ._stream import item_of
from ._types import LISTS

T = TypeVar("T")


class BoundedList(NativeCollection[T]):
    """A list of at most a field's capacity of elements, held as the field stores them.

    Declared as `Annotated[BoundedList[T], Capacity(n)]` or with
    `field(capacity=n)`, it is stored as `list[T]` is, and a read returns
    one. Indexing decodes or encodes one element, and setting an element
    checks the value as a write to the field would. A change that would
    leave more elements than the capacity raises `ValueError` and leaves
    the value as it was.

    `BoundedList[T](values, capacity=n)` builds one outside a box. Copies,
    slices, `+` and `*` keep the element type and the capacity, and a
    pickled value is rebuilt from its element type, capacity and bytes.
    """

    __slots__ = ()

    def __init__(self, values: Iterable[T] = (), *, capacity: int) -> None:
        """Build a value holding `values`.

        Raises
        ------
        TypeError
            Always: the element type is given by subscripting, as in
            `BoundedList[float](values, capacity=n)`.
        """
        raise TypeError(
            "BoundedList needs its element type, as in"
            " BoundedList[float](values, capacity=n)"
        )

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
        Callable[[object, int, bytes], BoundedList[Any]], tuple[object, int, bytes]
    ]:
        return _rebuild, (self.element_annotation, self.capacity, self._stored())


class _Alias(types.GenericAlias):
    """`BoundedList[T]`, which builds a value of element type `T` when called."""

    def __call__(
        self, values: Iterable[object] = (), *, capacity: int
    ) -> BoundedList[Any]:
        (element,) = self.__args__
        codec, empty = _codec(element, capacity)
        value = cast("BoundedList[Any]", codec.decode(0, bytes(empty)))
        value.extend(values)
        return value


def _name(element: object) -> str:
    return element.__qualname__ if isinstance(element, type) else repr(element)


@functools.lru_cache
def _codec(element: object, capacity: int) -> tuple[Types, int]:
    """Return a one-field codec of `BoundedList[element]` and the size of its empty stored form."""
    item = item_of(
        Annotated[BoundedList[element], Capacity(capacity)],  # type: ignore[valid-type]
        label=f"BoundedList[{_name(element)}]",
    )
    return item.types, item.spec.members[0].offset


def _rebuild(element: object, capacity: int, stored: bytes) -> BoundedList[Any]:
    """Return the BoundedList whose stored form is `stored`, as pickling gave it."""
    return cast("BoundedList[Any]", _codec(element, capacity)[0].decode(0, stored))


LISTS[BoundedList] = BoundedList
MutableSequence.register(BoundedList)
