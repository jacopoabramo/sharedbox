import operator
import uuid
from collections.abc import Callable, MutableSequence
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

import numpy as np
import pytest
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from sharedbox import BoundedList, Capacity, SharedBox


@dataclass(frozen=True)
class Spot:
    n: int
    x: float


def pair_class(element: object, capacity: int) -> type[SharedBox]:
    hint = Annotated[BoundedList[element], Capacity(capacity)]  # type: ignore[valid-type]
    return type(
        "Pair",
        (SharedBox,),
        {"__qualname__": "Pair", "__annotations__": {"first": hint, "second": hint}},
    )


INT64 = st.integers(-(2**63), 2**63 - 1)
# nan is left out because remove, index and the comparison with the mirror find
# an element by equality, which nan never has.
FLOAT = st.floats(allow_nan=False)
# Of no element type the machines use, so every encode refuses it.
WRONG = object()


class BoundedMachine(RuleBasedStateMachine):
    """A BoundedList read from a box against a list holding the same elements."""

    element: ClassVar[object]
    values: ClassVar[st.SearchStrategy[Any]]

    def __init__(self) -> None:
        super().__init__()
        self.name = ""
        self.box: Any = None
        self.capacity = 0
        self.bounded: Any = None
        self.mirror: list[Any] = []

    @initialize(capacity=st.integers(1, 64), data=st.data())
    def create(self, capacity: int, data: st.DataObject) -> None:
        """Create a box of two fields of the element type and read the first."""
        first = data.draw(st.lists(self.values, max_size=capacity))
        self.capacity = capacity
        self.name = f"sbtest-{uuid.uuid4().hex[:16]}"
        self.box = pair_class(self.element, capacity).create(self.name, first, [])
        self.bounded = self.box.first
        self.mirror = list(first)

    def change(self, mutate: Callable[[MutableSequence[Any]], object]) -> None:
        """Apply mutate to both values, or expect ValueError when the list it gives passes the capacity."""
        expected = list(self.mirror)
        result = mutate(expected)
        if len(expected) > self.capacity:
            with pytest.raises(ValueError, match="holds at most"):
                mutate(self.bounded)
            return
        assert mutate(self.bounded) == result
        self.mirror = expected

    def position(self, data: st.DataObject) -> int:
        return data.draw(st.integers(-len(self.mirror), len(self.mirror) - 1))

    def some(self, data: st.DataObject) -> list[Any]:
        return data.draw(st.lists(self.values, max_size=self.capacity + 2))

    @rule(data=st.data())
    def append(self, data: st.DataObject) -> None:
        """Append one element."""
        value = data.draw(self.values)
        self.change(lambda s: s.append(value))

    @rule(data=st.data())
    def insert(self, data: st.DataObject) -> None:
        """Insert one element at an index that may lie past either end."""
        at = data.draw(st.integers(-len(self.mirror) - 2, len(self.mirror) + 2))
        value = data.draw(self.values)
        self.change(lambda s: s.insert(at, value))

    @precondition(lambda self: self.mirror)
    @rule(data=st.data())
    def pop(self, data: st.DataObject) -> None:
        """Pop the last element or one at a drawn index."""
        at = data.draw(st.none() | st.integers(-len(self.mirror), len(self.mirror) - 1))
        self.change(lambda s: s.pop() if at is None else s.pop(at))

    @precondition(lambda self: self.mirror)
    @rule(data=st.data())
    def remove(self, data: st.DataObject) -> None:
        """Remove the first element equal to one the value holds."""
        value = data.draw(st.sampled_from(self.mirror))
        self.change(lambda s: s.remove(value))

    @rule(data=st.data())
    def extend(self, data: st.DataObject) -> None:
        """Extend with a list or a generator that may pass the capacity."""
        values = self.some(data)
        lazily = data.draw(st.booleans())
        self.change(lambda s: s.extend(iter(values) if lazily else values))

    @precondition(lambda self: self.mirror)
    @rule(data=st.data())
    def setitem(self, data: st.DataObject) -> None:
        """Replace one element."""
        at = self.position(data)
        value = data.draw(self.values)
        self.change(lambda s: operator.setitem(s, at, value))

    @precondition(lambda self: self.mirror)
    @rule(data=st.data())
    def delitem(self, data: st.DataObject) -> None:
        """Delete one element."""
        at = self.position(data)
        self.change(lambda s: operator.delitem(s, at))

    @rule()
    def write_the_value(self) -> None:
        """Write the value to the second field and check it stores the bytes the elements would."""
        self.box.second = self.bounded
        self.box.first = list(self.mirror)
        assert self.box.first._stored() == self.box.second._stored()
        self.bounded = self.box.first

    @rule(data=st.data())
    def get_slice(self, data: st.DataObject) -> None:
        """Read a slice as a BoundedList of the same elements."""
        part = data.draw(st.slices(len(self.mirror)))
        got = self.bounded[part]
        assert type(got) is BoundedList
        assert list(got) == self.mirror[part]

    @rule(data=st.data())
    def set_slice(self, data: st.DataObject) -> None:
        """Assign to a slice, with as many elements as it covers when its step is not 1."""
        part = data.draw(st.slices(len(self.mirror)))
        if part.step in (None, 1):
            values = self.some(data)
        else:
            size = len(range(*part.indices(len(self.mirror))))
            values = data.draw(st.lists(self.values, min_size=size, max_size=size))
        self.change(lambda s: operator.setitem(s, part, values))

    @rule(data=st.data())
    def delete_slice(self, data: st.DataObject) -> None:
        """Delete a slice."""
        part = data.draw(st.slices(len(self.mirror)))
        self.change(lambda s: operator.delitem(s, part))

    @rule()
    def reverse(self) -> None:
        """Reverse the elements in place."""
        self.change(lambda s: s.reverse())

    @rule()
    def clear(self) -> None:
        """Remove every element."""
        self.change(lambda s: s.clear())

    @rule(data=st.data())
    def add_in_place(self, data: st.DataObject) -> None:
        """Extend with += and keep the same object."""
        values = self.some(data)

        def add(s: MutableSequence[Any]) -> bool:
            before = s
            s += values
            return s is before

        self.change(add)

    @rule(data=st.data())
    def count_and_contains(self, data: st.DataObject) -> None:
        """Count and find an element that may be missing."""
        value = data.draw(self.values)
        assert self.bounded.count(value) == self.mirror.count(value)
        assert (value in self.bounded) == (value in self.mirror)

    @rule(data=st.data())
    def find(self, data: st.DataObject) -> None:
        """Find an element between a start and a stop that may lie past either end."""
        n = len(self.mirror)
        present = st.sampled_from(self.mirror) if self.mirror else self.values
        value = data.draw(present | self.values)
        start = data.draw(st.integers(-n - 3, n + 3))
        stop = data.draw(st.integers(-n - 3, n + 3))
        try:
            expected = self.mirror.index(value, start, stop)
        except ValueError:
            with pytest.raises(ValueError):
                self.bounded.index(value, start, stop)
        else:
            assert self.bounded.index(value, start, stop) == expected

    @rule()
    def read_reversed(self) -> None:
        """Iterate from the end."""
        assert list(reversed(self.bounded)) == self.mirror[::-1]

    @rule(data=st.data())
    def refuse_a_wrong_element(self, data: st.DataObject) -> None:
        """Refuse an extend or a slice assignment holding one element of another type, changing nothing."""
        n = len(self.mirror)
        how = data.draw(st.sampled_from(["extend", "slice", "extended slice"]))
        ends = st.none() | st.integers(-n - 2, n + 2)
        step = st.integers(2, 4) | st.integers(-4, -2)
        match how:
            case "extend":
                part = slice(n, n)
            case "slice":
                part = slice(data.draw(ends), data.draw(ends))
            case _:
                part = slice(data.draw(ends), data.draw(ends), data.draw(step))
        covered = len(range(*part.indices(n)))
        # Within the capacity, so the element type is what refuses the values.
        room = covered if how == "extended slice" else self.capacity - n + covered
        if room == 0:
            return
        size = room if how == "extended slice" else data.draw(st.integers(1, room))
        values = data.draw(st.lists(self.values, min_size=size, max_size=size))
        values[data.draw(st.integers(0, size - 1))] = WRONG
        with pytest.raises(TypeError):
            if how == "extend":
                self.bounded.extend(values)
            else:
                self.bounded[part] = values

    @invariant()
    def holds_the_mirror(self) -> None:
        """Check the value holds the mirror's elements in order."""
        if self.bounded is not None:
            assert list(self.bounded) == self.mirror

    def teardown(self) -> None:
        if self.box is not None:
            self.box.close()
            type(self.box).unlink(self.name)


class FloatMachine(BoundedMachine):
    element = float
    values = FLOAT


class Int8Machine(BoundedMachine):
    element = np.int8
    values = st.integers(-128, 127).map(np.int8)


class StrMachine(BoundedMachine):
    element = Annotated[str, Capacity(8)]
    values = st.text(max_size=8).map(lambda t: t.encode()[:8].decode("utf-8", "ignore"))


class SpotMachine(BoundedMachine):
    element = Spot
    values = st.builds(Spot, INT64, FLOAT)


class OptionalIntMachine(BoundedMachine):
    element = int | None
    values = st.none() | INT64


TestFloatMachine = FloatMachine.TestCase
TestInt8Machine = Int8Machine.TestCase
TestStrMachine = StrMachine.TestCase
TestSpotMachine = SpotMachine.TestCase
TestOptionalIntMachine = OptionalIntMachine.TestCase
