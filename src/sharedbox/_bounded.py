"""BoundedList, a list value that keeps the stored form of a list field."""

from __future__ import annotations

from collections.abc import MutableSequence
from typing import TYPE_CHECKING, Generic, TypeVar

from ._native import NativeCollection

if TYPE_CHECKING:
    from collections.abc import Iterator

T = TypeVar("T")


class BoundedList(NativeCollection, Generic[T]):
    """A list of at most a field's capacity of elements, held as the field stores them.

    Declared as `Annotated[BoundedList[T], Capacity(n)]` or with
    `field(capacity=n)`, it is stored as `list[T]` is, and a read returns
    one. Indexing decodes or encodes one element, and setting an element
    checks the value as a write to the field would.
    """

    __slots__ = ()

    if TYPE_CHECKING:

        def __getitem__(self, index: int, /) -> T: ...
        def __setitem__(self, index: int, value: T, /) -> None: ...
        def __iter__(self) -> Iterator[T]: ...


MutableSequence.register(BoundedList)
