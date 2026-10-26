import collections.abc
import copy
import datetime
import decimal
import enum
import gc
import pickle
import re
import weakref
from dataclasses import dataclass
from typing import Annotated, Any, Literal, NamedTuple

import pytest
from crossproc import call_in_child, update_in_child

from sharedbox import BoundedSet, Capacity, SharedBox, SharedStream
from sharedbox._layout import build_layout
from sharedbox._native import NativeSet

Name = Annotated[str, Capacity(16)]


class Catalogue(SharedBox):
    seen: Annotated[BoundedSet[Name], Capacity(8)]


class Tags(BoundedSet[Name]):
    __slots__ = ("tag",)
    tag: object


class Holder(BoundedSet[int]):
    pass


class Unreadable(BoundedSet[int]):
    __slots__ = ()

    def __iter__(self) -> collections.abc.Iterator[int]:
        raise AssertionError("a byte copy does not iterate the value")


@dataclass(frozen=True)
class Tagging:
    tags: Annotated[BoundedSet[int], Capacity(4)]


class SetPlaces(SharedBox):
    tagging: Tagging
    maybe: Annotated[BoundedSet[int], Capacity(4)] | None
    rows: Annotated[list[Annotated[BoundedSet[int], Capacity(2)]], Capacity(2)]


class Listed(SharedBox, identity="sbtest/bounded-set/attach"):
    seen: Annotated[set[Name], Capacity(8)]


class Bounded(SharedBox, identity="sbtest/bounded-set/attach"):
    seen: Annotated[BoundedSet[Name], Capacity(8)]


class Copies(SharedBox):
    source: Annotated[Unreadable, Capacity(8)]
    target: Annotated[BoundedSet[int], Capacity(8)]
    wide: Annotated[BoundedSet[int], Capacity(16)]


class Floats(SharedBox):
    values: Annotated[set[float], Capacity(8)]


class Either(SharedBox):
    value: Annotated[BoundedSet[int], Capacity(4)] | Annotated[str, Capacity(8)]


class Labels(SharedBox):
    tags: Annotated[Tags, Capacity(8)]


class Holders(SharedBox):
    holder: Annotated[Holder, Capacity(4)]


def make(annotation: object, name: str = "T", field: str = "x") -> type:
    return type(
        name, (), {"__annotations__": {field: annotation}, "__module__": "__main__"}
    )


def test_a_bounded_set_field_reads_back_its_values(unique_name: str) -> None:
    """Read a BoundedSet field back as a new BoundedSet holding the values written."""
    with Catalogue.create(unique_name, {"a", "b"}) as box:
        seen = box.seen
        assert isinstance(seen, BoundedSet)
        assert len(seen) == 2
        assert set(seen) == {"a", "b"}
        assert isinstance(seen, collections.abc.MutableSet)
        assert not isinstance(seen, collections.abc.MutableSequence)
        assert box.seen is not box.seen


def test_every_place_a_set_goes_reads_back_as_bounded_set(
    unique_name: str, names: collections.abc.Callable[[str], str]
) -> None:
    """Read a BoundedSet from a record member, a stream item, an optional and a list element."""
    with SetPlaces.create(
        unique_name,
        Tagging({1, 2}),  # type: ignore[arg-type]
        None,
        [{1}, {2, 3}],
    ) as box:
        assert box.maybe is None
        box.maybe = {5, 6}  # type: ignore[assignment]
        values: list[Any] = [box.tagging.tags, box.maybe, *box.rows]
        assert type(box.rows) is list
        with SharedStream.create(
            Annotated[BoundedSet[int], Capacity(4)], names("s"), capacity=2
        ) as stream:
            reader = stream.reader()
            with stream.sender() as sender:
                sender.send({9, 10})
            values.append(next(iter(reader)))
    assert all(type(v) is BoundedSet for v in values)
    assert [set(v) for v in values] == [{1, 2}, {5, 6}, {1}, {2, 3}, {9, 10}]


def test_a_set_written_over_an_older_one_stores_zero_bytes_after_its_elements(
    names: collections.abc.Callable[[str], str],
) -> None:
    """Store zero bytes past a set element's end in a stream slot that held a longer one."""
    with SharedStream.create(
        Annotated[BoundedSet[Name], Capacity(1)], names("s"), capacity=2
    ) as stream:
        items = iter(stream.reader())
        with stream.sender() as sender:
            for text in ("a longer text", "a longer text", "a"):
                sender.send({text})
                last = next(items)
    count_and_length = (1).to_bytes(4, "little") * 2
    assert last._stored() == count_and_length + b"a" + bytes(15)


def test_set_and_bounded_set_declarations_attach_to_each_other(
    unique_name: str,
) -> None:
    """Update from a spawned process through a BoundedSet declaration a box made with set."""
    with Listed.create(unique_name, {"a"}) as made:
        with Bounded.attach(unique_name) as attached:
            update_in_child(attached, seen={"b", "c"})
        assert made.seen == {"b", "c"}


def test_a_bounded_set_write_copies_its_bytes(unique_name: str) -> None:
    """Copy the bytes of a BoundedSet of the field's element type, encode one of another, and refuse one too large."""
    with (
        Copies.create(unique_name + "-a", {3, 1, 2}, set(), set(range(9))) as box,
        Floats.create(unique_name + "-b", set()) as floats,
    ):
        box.target = box.source
        assert box.target._stored() == box.source._stored()
        floats.values = box.target  # type: ignore[assignment]
        assert floats.values == {1.0, 2.0, 3.0}
        assert all(type(v) is float for v in floats.values)
        with pytest.raises(
            ValueError, match="holds at most 8 elements; the value has 9"
        ):
            box.target = box.wide
        assert set(box.target) == {1, 2, 3}


def test_a_bounded_set_that_was_never_built_is_refused(unique_name: str) -> None:
    """Refuse a BoundedSet made by the native __new__ alone and keep the field's value."""
    with Catalogue.create(unique_name, {"a"}) as box:
        with pytest.raises(
            TypeError, match=r"Catalogue.seen got a BoundedSet that was never built"
        ):
            box.update(seen=NativeSet.__new__(BoundedSet))
        assert set(box.seen) == {"a"}


def test_iterating_a_bounded_set_that_was_never_built_raises() -> None:
    """Raise TypeError when a BoundedSet made by the native __new__ alone is iterated."""
    with (
        pytest.raises(TypeError),
        pytest.warns(RuntimeWarning, match="uninitialized instance"),
    ):
        iter(NativeSet.__new__(BoundedSet))


class Letter(enum.StrEnum):
    A = "a"


class Raw(bytes, enum.Enum):
    A = b"a"


class Pair(NamedTuple):
    x: int


class Ratio(float, enum.Enum):
    ONE = 1.0


class Wave(complex, enum.Enum):
    ONE = 1 + 0j


class Day(datetime.date, enum.Enum):
    FIRST = 2026, 1, 1


class Origin(tuple[int], enum.Enum):
    ZERO = (0,)


class Plain(enum.Enum):
    A = 1


class Moment(datetime.datetime, enum.Enum):
    START = 2026, 1, 1


@dataclass(frozen=True)
class Price:
    amount: Annotated[decimal.Decimal, Capacity(8)]


@pytest.mark.parametrize(
    ("key", "reason"),
    [
        (Annotated[decimal.Decimal, Capacity(8)], "a Decimal"),
        (int | float, "a union of int:8 and float:8"),
        (bool | int, "a union of bool:1 and int:8"),
        (Annotated[frozenset[int], Capacity(4)], "a frozenset"),
        (float | Annotated[str, Capacity(4)], "a float:8 inside a union"),
        (Annotated[tuple[float, ...], Capacity(4)], "a float:8 inside a tuple[T, ...]"),
        (Letter | Annotated[str, Capacity(4)], "a union of enum(A) and str:4"),
        (Raw | Annotated[bytes, Capacity(4)], "a union of enum(A) and bytes:4"),
        (Pair | tuple[int], "a union of record(x:int:8) and tuple(int:8)"),
        (Literal[1, True], "a Literal of equal values 1 and True"),
        (Price, "a Decimal"),
        (Annotated[frozenset[int], Capacity(4)] | None, "a frozenset"),
        (Ratio | int, "a union of enum(ONE) and int:8"),
        (Wave | int, "a union of enum(ONE) and int:8"),
        (Day | datetime.date, "a union of enum(FIRST) and date"),
        (Origin | tuple[int], "a union of enum(ZERO) and tuple(int:8)"),
        (
            Annotated[int | Annotated[str, Capacity(4)], "nested"] | bool,
            "a union of union(int:8,str:4) and bool:1",
        ),
        (
            Annotated[int | None, "nested"] | bool,
            "a union of optional(int:8) and bool:1",
        ),
        (Literal[Ratio.ONE] | int, "a union of literal(member:ONE) and int:8"),
        (
            Literal[Day.FIRST] | datetime.date,
            "a union of literal(member:FIRST) and date",
        ),
        (
            # Literal unpacks a tuple given alone, so the member goes inside one.
            Literal[(Origin.ZERO,)] | tuple[int],
            "a union of literal(member:ZERO) and tuple(int:8)",
        ),
    ],
)
def test_keys_that_compare_unlike_their_bytes_are_refused(
    key: object, reason: str
) -> None:
    """Refuse a BoundedSet key type whose equal values may differ in bytes, naming why, which a builtin set still takes."""
    with pytest.raises(
        TypeError, match=re.escape(f"Box.keys: BoundedSet keys cannot hold {reason}")
    ):
        build_layout(
            make(Annotated[BoundedSet[key], Capacity(4)], "Box", "keys")  # type: ignore[valid-type]
        )
    build_layout(
        make(
            Annotated[set[Annotated[decimal.Decimal, Capacity(8)]], Capacity(4)],
            "Box",
            "keys",
        )
    )


@pytest.mark.parametrize(
    "key", [Plain | int, Literal[Plain.A] | int, Moment | datetime.date]
)
def test_union_members_whose_values_never_compare_equal_make_a_key(key: object) -> None:
    """Declare a BoundedSet of a union whose members' values never compare equal, as a plain Enum's and an int's."""
    build_layout(make(Annotated[BoundedSet[key], Capacity(4)], "Box", "keys"))  # type: ignore[valid-type]


def test_a_bounded_set_inside_a_union_reads_back(unique_name: str) -> None:
    """Read back a BoundedSet or a str from a union field, whichever was written."""
    with Either.create(unique_name, {1, 2}) as box:
        value = box.value
        assert isinstance(value, BoundedSet)
        assert 2 in value
        box.value = "x"
        assert box.value == "x"


def test_a_pickled_bounded_set_reads_back_in_a_spawned_process(
    unique_name: str,
) -> None:
    """Read back a pickled BoundedSet in a spawned process, and keep a subclass and its slots through pickle and deepcopy."""
    with (
        Catalogue.create(unique_name + "-a", {"a", "b", "c"}) as box,
        Labels.create(unique_name + "-b", {"x"}) as labels,
    ):
        value = box.seen
        tags = labels.tags
    assert call_in_child(repr, value) == repr(value)
    tags.tag = "kept"
    for again in (pickle.loads(pickle.dumps(tags)), copy.deepcopy(tags)):
        assert type(again) is Tags
        assert again.tag == "kept"
        assert set(again) == {"x"}


def test_a_stored_count_past_the_capacity_reads_as_corrupt() -> None:
    """Raise ValueError naming the field when a set's stored count passes its capacity."""
    layout = build_layout(make(Annotated[BoundedSet[int], Capacity(8)]))
    data = (9).to_bytes(4, "little") + bytes(4 + 8 * 8)
    with pytest.raises(ValueError, match="T.x: the stored value"):
        layout.types.decode(0, data)


def test_a_cycle_through_a_value_or_its_iterator_is_collected(
    unique_name: str,
) -> None:
    """Free a value that holds itself, and one that holds an iterator over itself."""
    with Holders.create(unique_name, {1}) as box:
        holder, iterated = box.holder, box.holder
    holder.me = holder  # type: ignore[attr-defined]
    iterated.it = iter(iterated)  # type: ignore[attr-defined]
    refs = [weakref.ref(holder), weakref.ref(iterated)]
    del holder, iterated
    gc.collect()
    assert [ref() for ref in refs] == [None, None]
