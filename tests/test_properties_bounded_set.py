import datetime
import enum
import operator
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from sharedbox import BoundedSet, Capacity, SharedBox

Text = Annotated[str, Capacity(8)]


class Colour(enum.Enum):
    RED = 1
    GREEN = 2
    BLUE = 3
    GREY = 4


@dataclass(frozen=True)
class Entry:
    n: int
    name: Text


def pair_class(element: object, capacity: int) -> type[SharedBox]:
    hint = Annotated[BoundedSet[element], Capacity(capacity)]  # type: ignore[valid-type]
    return type(
        "Pair",
        (SharedBox,),
        {"__qualname__": "Pair", "__annotations__": {"first": hint, "second": hint}},
    )


# Small ranges, so the keys drawn repeat and every rule meets keys already held.
SMALL_INT = st.integers(-40, 40) | st.sampled_from([-(2**63), 2**63 - 1])
# Naive, as a key must be; its hours 0 to 5 include one a clock set back repeats.
DAY = datetime.datetime(2026, 10, 25)  # noqa: DTZ001
TEXT = st.text("abcé", max_size=4)


class SetMachine(RuleBasedStateMachine):
    """A BoundedSet read from a box against a set holding the same keys."""

    element: ClassVar[object]
    values: ClassVar[st.SearchStrategy[Any]]

    def __init__(self) -> None:
        super().__init__()
        self.name = ""
        self.box: Any = None
        self.capacity = 0
        self.bounded: Any = None
        self.mirror: set[Any] = set()

    @initialize(capacity=st.integers(1, 64), data=st.data())
    def create(self, capacity: int, data: st.DataObject) -> None:
        """Create a box of two fields of the key type and read the first."""
        first = set(data.draw(st.lists(self.values, max_size=capacity)))
        self.capacity = capacity
        self.name = f"sbtest-{uuid.uuid4().hex[:16]}"
        self.box = pair_class(self.element, capacity).create(self.name, first, set())
        self.bounded = self.box.first
        self.mirror = first

    def change(self, mutate: Callable[[Any], object]) -> None:
        """Apply mutate to both values, or expect ValueError when the set it gives passes the capacity."""
        expected = set(self.mirror)
        result = mutate(expected)
        if len(expected) > self.capacity:
            with pytest.raises(ValueError, match="holds at most"):
                mutate(self.bounded)
            return
        assert mutate(self.bounded) == result
        self.mirror = expected

    @rule(data=st.data())
    def add(self, data: st.DataObject) -> None:
        """Add one key that may be held already."""
        value = data.draw(self.values)
        self.change(lambda s: s.add(value))

    @rule(data=st.data())
    def discard(self, data: st.DataObject) -> None:
        """Discard one key that may be missing."""
        value = data.draw(self.values)
        self.change(lambda s: s.discard(value))

    @precondition(lambda self: self.mirror)
    @rule(data=st.data())
    def remove(self, data: st.DataObject) -> None:
        """Remove one key the value holds."""
        value = data.draw(st.sampled_from(list(self.mirror)))
        self.change(lambda s: s.remove(value))

    @precondition(lambda self: self.mirror)
    @rule()
    def pop(self) -> None:
        """Pop a key, which the mirror then loses too."""
        value = self.bounded.pop()
        self.mirror.remove(value)

    @rule()
    def clear(self) -> None:
        """Remove every key."""
        self.change(lambda s: s.clear())

    @rule(data=st.data())
    def update(self, data: st.DataObject) -> None:
        """Update from several iterables whose distinct new keys may pass the capacity."""
        parts = data.draw(
            st.lists(st.lists(self.values, max_size=self.capacity + 2), max_size=3)
        )
        self.change(lambda s: s.update(*parts))

    def operand(self, data: st.DataObject) -> tuple[Any, set[Any]]:
        """Draw keys that may pass the capacity, as a builtin set or a BoundedSet, and the set of them."""
        keys = set(data.draw(st.lists(self.values, max_size=self.capacity + 2)))
        if data.draw(st.booleans()):
            element: Any = self.element
            return BoundedSet[element](keys, capacity=self.capacity + 2), keys
        return keys, keys

    @rule(
        op=st.sampled_from([operator.or_, operator.and_, operator.sub, operator.xor]),
        reflected=st.booleans(),
        data=st.data(),
    )
    def combine(
        self, op: Callable[[Any, Any], Any], reflected: bool, data: st.DataObject
    ) -> None:
        """Combine with a drawn operand on either side, as the mirror combines with its keys."""
        other, keys = self.operand(data)
        left, right = (other, self.bounded) if reflected else (self.bounded, other)
        expected = op(keys, self.mirror) if reflected else op(self.mirror, keys)
        # The left operand builds the result unless it is a builtin set.
        maker = right if type(left) is set else left
        if len(expected) > maker.capacity:
            with pytest.raises(ValueError, match="holds at most"):
                op(left, right)
            return
        result = op(left, right)
        assert type(result) is type(maker)
        assert result.capacity == maker.capacity
        assert result == expected

    @rule(
        op=st.sampled_from([operator.ior, operator.iand, operator.isub, operator.ixor]),
        data=st.data(),
    )
    def combine_in_place(
        self, op: Callable[[Any, Any], Any], data: st.DataObject
    ) -> None:
        """Combine in place with a drawn operand, or expect ValueError past the capacity."""
        other, keys = self.operand(data)
        expected = op(set(self.mirror), keys)
        if len(expected) > self.capacity:
            with pytest.raises(ValueError, match="holds at most"):
                op(self.bounded, other)
            return
        assert op(self.bounded, other) is self.bounded
        self.mirror = expected

    @rule(data=st.data())
    def compare(self, data: st.DataObject) -> None:
        """Order the value against a drawn operand both ways round, and test them for common keys."""
        other, keys = self.operand(data)
        for op in (operator.le, operator.lt, operator.ge, operator.gt):
            assert op(self.bounded, other) == op(self.mirror, keys)
            assert op(other, self.bounded) == op(keys, self.mirror)
        assert self.bounded.isdisjoint(other) == self.mirror.isdisjoint(keys)

    @rule(data=st.data())
    def contains(self, data: st.DataObject) -> None:
        """Look up a key that may be missing."""
        value = data.draw(self.values)
        assert (value in self.bounded) == (value in self.mirror)

    @rule()
    def write_and_read(self) -> None:
        """Write the value to the second field and read it back."""
        self.box.second = self.bounded
        self.bounded = self.box.second

    @invariant()
    def holds_the_mirror(self) -> None:
        """Check the value holds the mirror's keys and compares equal to it."""
        if self.bounded is not None:
            assert set(self.bounded) == self.mirror
            assert self.bounded == self.mirror

    def teardown(self) -> None:
        if self.box is not None:
            self.box.close()
            type(self.box).unlink(self.name)


class IntMachine(SetMachine):
    element = int
    values = SMALL_INT


class StrMachine(SetMachine):
    element = Text
    values = TEXT


class FloatMachine(SetMachine):
    element = float
    # nan is left out because a key has to equal itself; -0.0 is drawn on purpose.
    values = st.sampled_from([-0.0, 0.0, 1.5, -2.0]) | st.floats(-4, 4, allow_nan=False)


class DatetimeMachine(SetMachine):
    element = datetime.datetime
    values = st.builds(
        lambda hour, fold: DAY.replace(hour=hour, minute=30, fold=fold),
        st.integers(0, 5),
        st.integers(0, 1),
    )


class EnumMachine(SetMachine):
    element = Colour
    values = st.sampled_from(Colour)


class EntryMachine(SetMachine):
    element = Entry
    values = st.builds(Entry, st.integers(0, 6), st.sampled_from(["", "a", "bc"]))


TestIntMachine = IntMachine.TestCase
TestStrMachine = StrMachine.TestCase
TestFloatMachine = FloatMachine.TestCase
TestDatetimeMachine = DatetimeMachine.TestCase
TestEnumMachine = EnumMachine.TestCase
TestEntryMachine = EntryMachine.TestCase
