"""BoundedList, BoundedSet and BoundedDict, list, set and dict values that keep the stored form of their field."""

from __future__ import annotations

import copy
import functools
import types
from collections.abc import Callable, Iterable, Mapping
from typing import Annotated, Any, Self, TypeVar, cast

from ._layout import Capacity
from ._native import NativeCollection, NativeDict, NativeSet, Types
from ._stream import item_of
from ._types import bounded_args

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")
C = TypeVar("C", bound=NativeCollection[Any] | NativeSet[Any] | NativeDict[Any, Any])


def _copy(self: C) -> C:
    """Return a copy, with a shallow copy of a subclass's attributes."""
    value = cast("C", self.copy())
    _restore(value, self.__getstate__())
    return value


def _deepcopy(self: C, memo: dict[int, Any]) -> C:
    """Return a copy, with a deep copy of a subclass's attributes.

    The elements are copied as bytes, since every read decodes an element
    anew and the copy shares none with this value.
    """
    value = cast("C", self.copy())
    memo[id(self)] = value
    _restore(value, copy.deepcopy(self.__getstate__(), memo))
    return value


def _reduce(
    self: NativeCollection[Any] | NativeSet[Any] | NativeDict[Any, Any],
) -> tuple[
    Callable[[type[Any], object, int, bytes], Any],
    tuple[type[Any], object, int, bytes],
    object,
]:
    """Return how pickling rebuilds the value: its class, element type, capacity and stored form."""
    return (
        _rebuild,
        (type(self), self._element_annotation, self.capacity, self._stored()),
        self.__getstate__(),
    )


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

    A list of `int`, `float`, `bool`, `complex` or NumPy numbers other than
    `datetime64` supports the buffer protocol: `np.asarray(value)` and
    `memoryview(value)` share its elements without decoding them, read only
    for `bool`. While such a view exists, a change of length raises
    `BufferError`.
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
        return _build(cls, _element(cls), values, capacity)

    def __init__(self, values: Iterable[T] = (), *, capacity: int) -> None:
        """Do nothing; `__new__` has built the value."""

    def __class_getitem__(cls, element: object) -> types.GenericAlias:
        """Return `cls` with its element type `element`.

        Raises
        ------
        TypeError
            If `element` is several types, or `cls` already fixes one.
        """
        return _parametrise(cls, element)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}[{_name(self._element_annotation)}]"
            f"({list(self)!r}, capacity={self.capacity})"
        )

    __copy__ = _copy
    __deepcopy__ = _deepcopy
    __reduce__ = _reduce


class BoundedSet(NativeSet[T]):
    """A set of at most a field's capacity of elements, held as the field stores them.

    Declared as `Annotated[BoundedSet[T], Capacity(n)]` or with
    `field(capacity=n)`, it is stored as `set[T]` is, and a read returns
    one. It iterates in the order its elements are stored, which may
    differ from a builtin `set`'s.

    Elements are compared by their stored bytes, all of them but the fold
    of a naive `datetime` or `time`, so a declaration refuses an element
    type whose equal values could be stored differently: a `Decimal`, a
    `frozenset`, a union whose members can hold equal values, such as
    `int | float` or a `StrEnum` and `str`, a `Literal` of two equal values
    such as `Literal[1, True]`, a record whose equality skips a member, as
    `field(compare=False)` does, and a float, complex or time type inside a
    union or a `tuple[T, ...]`. For the same reason, a set of a record
    class with its own `__eq__` may hold two elements that class counts as
    equal.

    Adding a nan, a NaT, or an aware `datetime` or `time` raises
    `ValueError`, and `-0.0` is stored as `0.0`. A naive `datetime` keeps
    the fold it was added with, and is the same element as that time at
    the other fold. A value that cannot be stored as an element is in no
    set: `in` returns False and `remove` raises `KeyError`.

    The operators `|`, `&`, `-` and `^` and their in-place forms take any
    set and return a value of this class and capacity, on either side of
    the operator, and the comparisons take any set. `&`, `value - other`
    and the comparisons treat an element of the other set that this one
    cannot hold as absent; `|`, `^` and `other - value` raise the error
    that adding it would.

    A subclass is declared and read back the same way, generic as
    `class MySet(BoundedSet[T])` used as `MySet[int]`, or with its element
    type fixed as `class Tags(BoundedSet[int])`. A read from a box calls
    neither `__new__` nor `__init__`. `copy.copy`, `copy.deepcopy` and
    pickling keep a subclass's attributes, as for a `set` subclass.
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

        Other arguments are left to `__init__`, so a subclass's `__init__`
        may take more. It receives the same `values` object after `__new__`
        has consumed it, so a generator arrives empty.

        Raises
        ------
        TypeError
            If `cls` fixes no element type, as `BoundedSet` alone does, or
            an element is not of that type.
        ValueError
            If an element does not fit, or there are more than `capacity`.
        """
        return _build(cls, _element(cls), values, capacity)

    def __init__(self, values: Iterable[T] = (), *, capacity: int) -> None:
        """Do nothing; `__new__` has built the value."""

    def __class_getitem__(cls, element: object) -> types.GenericAlias:
        """Return `cls` with its element type `element`.

        Raises
        ------
        TypeError
            If `element` is several types, or `cls` already fixes one.
        """
        return _parametrise(cls, element)

    def __repr__(self) -> str:
        elements = ", ".join(map(repr, self))
        shown = f"{{{elements}}}" if elements else "set()"
        return (
            f"{type(self).__name__}[{_name(self._element_annotation)}]"
            f"({shown}, capacity={self.capacity})"
        )

    __copy__ = _copy
    __deepcopy__ = _deepcopy
    __reduce__ = _reduce


class BoundedDict(NativeDict[K, V]):
    """A dict of at most a field's capacity of items, held as the field stores them.

    Declared as `Annotated[BoundedDict[K, V], Capacity(n)]` or with
    `field(capacity=n)`, it is stored as `dict[K, V]` is, and a read
    returns one. It keeps its items in insertion order, as a `dict` does.
    A change that would leave more keys than the capacity raises
    `ValueError` and leaves the value as it was. `|` takes any mapping on
    either side and returns a value of this class and capacity.

    Keys follow the rules of `BoundedSet` elements: they are compared by
    their stored bytes, so a declaration refuses the same key types, and a
    nan, a NaT, or an aware `datetime` or `time` key raises `ValueError`.
    A key that cannot be stored is in no dict: `in` returns False and
    `d[key]` raises `KeyError`. Values may be of any type a `dict` field
    takes, and `==` compares them as `dict` does.

    A subclass is declared and read back the same way, generic as
    `class MyDict(BoundedDict[K, V])` used as `MyDict[int, float]`, or
    with its types fixed as `class Weights(BoundedDict[int, float])`. A
    read from a box calls neither `__new__` nor `__init__`. `copy.copy`,
    `copy.deepcopy` and pickling keep a subclass's attributes, as for a
    `dict` subclass.
    """

    __slots__ = ()

    def __new__(
        cls,
        values: Mapping[K, V] | Iterable[tuple[K, V]] = (),
        *args: Any,
        capacity: int,
        **kwargs: Any,
    ) -> Self:
        """Build a value of `capacity` holding `values`, a mapping or pairs, of the key and value types `cls` fixes.

        Other arguments are left to `__init__`, so a subclass's `__init__`
        may take more. It receives the same `values` object after `__new__`
        has consumed it, so a generator arrives empty.

        Raises
        ------
        TypeError
            If `cls` fixes no key and value types, as `BoundedDict` alone
            does, or a key or value is not of its type.
        ValueError
            If a key or value does not fit, or there are more than
            `capacity` keys.
        """
        return _build(cls, _element(cls), values, capacity)

    def __init__(
        self,
        values: Mapping[K, V] | Iterable[tuple[K, V]] = (),
        *,
        capacity: int,
    ) -> None:
        """Do nothing; `__new__` has built the value."""

    def __class_getitem__(cls, params: object) -> types.GenericAlias:
        """Return `cls` with its key and value types `params`.

        Raises
        ------
        TypeError
            If `params` is not two types, or `cls` already fixes them.
        """
        return _parametrise(cls, params)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}[{_name(self._element_annotation)}]"
            f"({dict(self.items())!r}, capacity={self.capacity})"
        )

    __copy__ = _copy
    __deepcopy__ = _deepcopy
    __reduce__ = _reduce


class _Alias(types.GenericAlias):
    """`BoundedList[T]`, `BoundedSet[T]`, `BoundedDict[K, V]` or a subclass's, which builds a value of those types when called."""

    def __call__(
        self,
        values: Iterable[object] = (),
        *args: Any,
        capacity: int,
        **kwargs: Any,
    ) -> Any:
        cls = cast("type[Any]", self.__origin__)
        value = _build(cls, _element(self), values, capacity)
        cls.__init__(value, values, *args, capacity=capacity, **kwargs)
        return value


def _element(hint: object) -> object:
    """Return the element type `hint` or its bases give, the tuple of a dict's key and value types, or None."""
    args = bounded_args(hint)
    if args is None:
        return None
    return args[0] if len(args) == 1 else args


def _parametrise(cls: type, element: object) -> types.GenericAlias:
    """Return `cls` with its element type, or a dict's key and value types, `element`, as `__class_getitem__` does."""
    mapping = issubclass(cls, NativeDict)
    if mapping:
        given = len(element) if isinstance(element, tuple) else 1
        if given != 2:
            raise TypeError(f"{cls.__name__} takes a key and a value type, got {given}")
    # typing rebuilds an alias it evaluates with its arguments as a tuple.
    elif isinstance(element, tuple):
        if len(element) != 1:
            raise TypeError(
                f"{cls.__name__} takes one element type, got {len(element)}"
            )
        (element,) = element
    fixed = _element(cls)
    if fixed is not None:
        held = "key and value types" if mapping else "element type"
        raise TypeError(f"{cls.__name__} already has {held} {_name(fixed)}")
    return _Alias(cls, element)


def _restore(value: object, state: object) -> None:
    """Set the attributes `object.__getstate__` gave on `value`, as unpickling sets them."""
    attrs, slots = state if isinstance(state, tuple) else (state, None)
    if attrs:
        value.__dict__.update(attrs)
    for name, member in (slots or {}).items():
        setattr(value, name, member)


def _name(element: object) -> str:
    if isinstance(element, tuple):
        return ", ".join(map(_name, element))
    return element.__qualname__ if isinstance(element, type) else repr(element)


def _build(cls: type[C], element: object, values: Iterable[Any], capacity: int) -> C:
    """Return a new `cls` of `capacity` holding `values`, or raise `TypeError` when `element` is None."""
    listed = issubclass(cls, NativeCollection)
    if element is None:
        mapping = issubclass(cls, NativeDict)
        needs = "key and value types" if mapping else "element type"
        example = "float" if listed else "int, float" if mapping else "int"
        raise TypeError(
            f"{cls.__name__} needs its {needs}, as in"
            f" {cls.__name__}[{example}](values, capacity=n)"
        )
    codec, empty = _codec(cls, element, capacity)
    value: Any = codec.decode(0, bytes(empty))
    if listed:
        value.extend(values)
    else:
        value.update(values)
    return cast("C", value)


def _new_codec(cls: type, element: object, capacity: int) -> tuple[Types, int]:
    """Return a one-field codec of `cls` holding `element` and the size of its empty stored form."""
    item = item_of(
        Annotated[_Alias(cls, element), Capacity(capacity)],
        label=f"{cls.__name__}[{_name(element)}]",
    )
    # The slots start at the alignment, which is at least 4, the size of the count;
    # a dict's first member offset is its key's place in a slot, not the slots'.
    return item.types, item.spec.alignment


_cached_codec = functools.lru_cache(_new_codec)


def _codec(cls: type, element: object, capacity: int) -> tuple[Types, int]:
    """Return `_new_codec(cls, element, capacity)`, cached when `element` can be hashed."""
    try:
        hash(element)
    except TypeError:
        return _new_codec(cls, element, capacity)
    return _cached_codec(cls, element, capacity)


def _rebuild(cls: type[Any], element: object, capacity: int, stored: bytes) -> Any:
    """Return the `cls` whose stored form is `stored`, as pickling gave it."""
    return _codec(cls, element, capacity)[0].decode(0, stored)
