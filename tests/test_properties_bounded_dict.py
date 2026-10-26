import datetime
import enum
import math
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

from sharedbox import BoundedDict, Capacity, SharedBox

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


def pair_class(key: object, value: object, capacity: int) -> type[SharedBox]:
    hint = Annotated[BoundedDict[key, value], Capacity(capacity)]  # type: ignore[valid-type]
    return type(
        "Pair",
        (SharedBox,),
        {"__qualname__": "Pair", "__annotations__": {"first": hint, "second": hint}},
    )


def same(a: object, b: object) -> bool:
    """Return whether a and b are equal, counting two nan floats as equal."""
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(map(same, a, b))
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a):
        return math.isnan(b)
    return a == b


# Small ranges, so the keys drawn repeat and every rule meets keys already held.
SMALL_INT = st.integers(-40, 40) | st.sampled_from([-(2**63), 2**63 - 1])
# Naive, as a key must be; its hours 0 to 5 include one a clock set back repeats.
DAY = datetime.datetime(2026, 10, 25)  # noqa: DTZ001
TEXT = st.text("abcé", max_size=4)
FLOAT_VALUES = st.floats(allow_nan=True) | st.just(math.nan)


class DictMachine(RuleBasedStateMachine):
    """A BoundedDict read from a box against a dict holding the same items."""

    key: ClassVar[object]
    keys: ClassVar[st.SearchStrategy[Any]]
    value: ClassVar[object]
    values: ClassVar[st.SearchStrategy[Any]]

    def __init__(self) -> None:
        super().__init__()
        self.name = ""
        self.box: Any = None
        self.capacity = 0
        self.bounded: Any = None
        self.mirror: dict[Any, Any] = {}

    def items(self, data: st.DataObject, most: int) -> list[tuple[Any, Any]]:
        return data.draw(st.lists(st.tuples(self.keys, self.values), max_size=most))

    @initialize(capacity=st.integers(1, 64), data=st.data())
    def create(self, capacity: int, data: st.DataObject) -> None:
        """Create a box of two fields of the dict type and read the first."""
        first = dict(self.items(data, capacity))
        self.capacity = capacity
        self.name = f"sbtest-{uuid.uuid4().hex[:16]}"
        self.box = pair_class(self.key, self.value, capacity).create(
            self.name, first, {}
        )
        self.bounded = self.box.first
        self.mirror = first

    def change(self, mutate: Callable[[Any], object]) -> None:
        """Apply mutate to both values, or expect ValueError when the dict it gives passes the capacity."""
        expected = dict(self.mirror)
        result = mutate(expected)
        if len(expected) > self.capacity:
            with pytest.raises(ValueError, match="holds at most"):
                mutate(self.bounded)
            return
        assert same(mutate(self.bounded), result)
        self.mirror = expected

    @rule(data=st.data())
    def setitem(self, data: st.DataObject) -> None:
        """Set the value of a key that may be held already."""
        key, value = data.draw(self.keys), data.draw(self.values)
        self.change(lambda d: d.__setitem__(key, value))

    @precondition(lambda self: self.mirror)
    @rule(data=st.data())
    def replace(self, data: st.DataObject) -> None:
        """Replace the value of a key the dict holds."""
        key = data.draw(st.sampled_from(list(self.mirror)))
        value = data.draw(self.values)
        self.change(lambda d: d.__setitem__(key, value))

    @precondition(lambda self: self.mirror)
    @rule(data=st.data())
    def delitem(self, data: st.DataObject) -> None:
        """Delete a key the dict holds."""
        key = data.draw(st.sampled_from(list(self.mirror)))
        self.change(lambda d: d.__delitem__(key))

    @rule(data=st.data())
    def pop(self, data: st.DataObject) -> None:
        """Pop a key that may be missing, with a default."""
        key, fallback = data.draw(self.keys), data.draw(self.values)
        self.change(lambda d: d.pop(key, fallback))

    @precondition(lambda self: self.mirror)
    @rule()
    def popitem(self) -> None:
        """Pop the last item."""
        self.change(lambda d: d.popitem())

    @rule(data=st.data())
    def setdefault(self, data: st.DataObject) -> None:
        """Set a default for a key that may be held already."""
        key, fallback = data.draw(self.keys), data.draw(self.values)
        self.change(lambda d: d.setdefault(key, fallback))

    @rule(data=st.data(), as_mapping=st.booleans())
    def update(self, data: st.DataObject, as_mapping: bool) -> None:
        """Update from a mapping or from pairs whose new keys may pass the capacity."""
        pairs = self.items(data, self.capacity + 2)
        other = dict(pairs) if as_mapping else pairs
        self.change(lambda d: d.update(other))

    @rule(data=st.data(), bounded=st.booleans())
    def merge_in_place(self, data: st.DataObject, bounded: bool) -> None:
        """Merge in place a builtin dict or a BoundedDict whose new keys may pass the capacity."""
        other = dict(self.items(data, self.capacity + 2))
        key: Any = self.key
        value: Any = self.value
        operand = (
            BoundedDict[key, value](other, capacity=self.capacity + 2)
            if bounded
            else other
        )

        def merge(d: Any) -> None:
            d |= operand

        self.change(merge)

    @rule()
    def clear(self) -> None:
        """Remove every item."""
        self.change(lambda d: d.clear())

    @rule(data=st.data())
    def get(self, data: st.DataObject) -> None:
        """Look up a key that may be missing."""
        key = data.draw(self.keys)
        assert same(self.bounded.get(key), self.mirror.get(key))
        assert (key in self.bounded) == (key in self.mirror)

    @rule()
    def write_and_read(self) -> None:
        """Write the value to the second field and read it back."""
        self.box.second = self.bounded
        self.bounded = self.box.second

    @invariant()
    def holds_the_mirror(self) -> None:
        """Check the value holds the mirror's items in the mirror's order."""
        if self.bounded is not None:
            assert same(tuple(self.bounded.items()), tuple(self.mirror.items()))

    def teardown(self) -> None:
        if self.box is not None:
            self.box.close()
            type(self.box).unlink(self.name)


class IntMachine(DictMachine):
    key = int
    keys = SMALL_INT
    value = float
    values = FLOAT_VALUES


class StrMachine(DictMachine):
    key = Text
    keys = TEXT
    value = Text
    values = TEXT


class FloatMachine(DictMachine):
    key = float
    # nan is left out because a key has to equal itself; -0.0 is drawn on purpose.
    keys = st.sampled_from([-0.0, 0.0, 1.5, -2.0]) | st.floats(-4, 4, allow_nan=False)
    value = float
    values = FLOAT_VALUES


class DatetimeMachine(DictMachine):
    key = datetime.datetime
    keys = st.builds(
        lambda hour, fold: DAY.replace(hour=hour, minute=30, fold=fold),
        st.integers(0, 5),
        st.integers(0, 1),
    )
    value = Text
    values = TEXT


class EnumMachine(DictMachine):
    key = Colour
    keys = st.sampled_from(Colour)
    value = float
    values = FLOAT_VALUES


class EntryMachine(DictMachine):
    key = Entry
    keys = st.builds(Entry, st.integers(0, 6), st.sampled_from(["", "a", "bc"]))
    value = Text
    values = TEXT


TestIntMachine = IntMachine.TestCase
TestStrMachine = StrMachine.TestCase
TestFloatMachine = FloatMachine.TestCase
TestDatetimeMachine = DatetimeMachine.TestCase
TestEnumMachine = EnumMachine.TestCase
TestEntryMachine = EntryMachine.TestCase
