"""BoundedList, a list value that keeps the stored form of a list field."""

from __future__ import annotations

from collections.abc import MutableSequence
from typing import TypeVar

from ._native import NativeCollection

T = TypeVar("T")


class BoundedList(NativeCollection[T]):
    """A list of at most a field's capacity of elements, held as the field stores them.

    Declared as `Annotated[BoundedList[T], Capacity(n)]` or with
    `field(capacity=n)`, it is stored as `list[T]` is, and a read returns
    one. Indexing decodes or encodes one element, and setting an element
    checks the value as a write to the field would. A change that would
    leave more elements than the capacity raises `ValueError` and leaves
    the value as it was.
    """

    __slots__ = ()


MutableSequence.register(BoundedList)
