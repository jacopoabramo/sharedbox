import collections.abc
import copy
import datetime
import decimal
import enum
import gc
import math
import operator
import pickle
import re
import struct
import threading
import weakref
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, NamedTuple

import attrs
import numpy as np
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


class Instant(datetime.datetime, enum.Enum):
    START = 2026, 1, 1


class Span(datetime.timedelta, enum.Enum):
    DAY = 1


class Amount(decimal.Decimal, enum.Enum):
    ONE = 1


@dataclass(frozen=True)
class Label:
    name: Name
    note: Name = field(compare=False)


@attrs.frozen
class Mark:
    x: int
    y: int = attrs.field(eq=False)


@attrs.frozen
class Fold:
    name: Name = attrs.field(eq=str.lower)


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
        (Moment | Instant, "a union of enum(START) and enum(START)"),
        (
            Literal[Moment.START] | Instant,
            "a union of literal(member:START) and enum(START)",
        ),
        (Span | datetime.timedelta, "a union of enum(DAY) and timedelta"),
        (Amount | int, "a union of enum(ONE) and int:8"),
        (Label, "a Label whose equality skips note"),
        (Mark, "a Mark whose equality skips y"),
        (Fold, "a Fold whose equality compares name through a function"),
        (Label | None, "a Label whose equality skips note"),
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


@dataclass(frozen=True)
class Gate:
    shut: bool

    def __post_init__(self) -> None:
        if self.shut:
            raise ValueError("the gate is shut")


class Gates(SharedBox):
    gates: Annotated[BoundedSet[Gate], Capacity(4)]


class Times(SharedBox):
    when: Annotated[BoundedSet[datetime.datetime], Capacity(4)]


@dataclass(frozen=True)
class Reading:
    n: int
    x: float
    at: datetime.datetime


# A wall time that a clock set back passes twice, so it exists at fold 0 and 1.
REPEATED = datetime.datetime(2026, 10, 25, 2, 30)  # noqa: DTZ001


class Red(enum.Enum):
    A = 1


class Blue(enum.Enum):
    A = 1


def keys_field(key: object, capacity: int = 16) -> Any:
    """Return the layout of a class `Catalogue` with one field `keys`, a BoundedSet of `key`."""
    return build_layout(
        make(Annotated[BoundedSet[key], Capacity(capacity)], "Catalogue", "keys")  # type: ignore[valid-type]
    )


def empty_value(key: object) -> Any:
    """Return an empty BoundedSet of `key` read from the field `Catalogue.keys`."""
    return keys_field(key).types.decode(0, bytes(64))


def forged(key: object, slots: list[bytes]) -> Any:
    """Return a BoundedSet of `key` read from stored slots `slots`, as another program could write them."""
    types = keys_field(key, max(16, len(slots))).types
    head = bytearray(types.decode(0, bytes(64))._stored())
    head[:4] = len(slots).to_bytes(4, "little")
    return types.decode(0, bytes(head) + b"".join(slots))


def test_a_bounded_set_is_built_outside_a_box() -> None:
    """Build a BoundedSet from values and refuse one past its capacity or of the wrong element type."""
    assert BoundedSet[int]([1, 2, 1], capacity=2) == {1, 2}
    assert BoundedSet[int](capacity=4) == set()
    with pytest.raises(
        ValueError,
        match=r"BoundedSet\[int\] holds at most 2 elements; the value would have 3",
    ):
        BoundedSet[int]([1, 2, 3], capacity=2)
    with pytest.raises(TypeError, match=r"BoundedSet\[int\] expects int, got str"):
        BoundedSet[int](["x"], capacity=4)  # type: ignore[list-item]
    with pytest.raises(TypeError, match=r"BoundedSet\[int\]\(values, capacity=n\)"):
        BoundedSet([1], capacity=4)


def test_repr_shows_the_element_type_and_capacity() -> None:
    """Show the element type, the elements in slot order and the capacity, with a subclass's own name."""
    assert (
        repr(BoundedSet[int]([2, 1], capacity=4))
        == "BoundedSet[int]({2, 1}, capacity=4)"
    )
    assert repr(Holder([2, 1], capacity=4)) == "Holder[int]({2, 1}, capacity=4)"


@pytest.mark.parametrize(
    ("key", "good", "bad", "message"),
    [
        (float, 1.0, float("nan"), "takes no nan keys"),
        (
            np.datetime64,
            np.datetime64(1, "ns"),
            np.datetime64("NaT", "ns"),
            "takes no NaT keys",
        ),
        (
            datetime.datetime,
            REPEATED,
            datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            "takes only naive datetime keys",
        ),
        (
            datetime.time,
            datetime.time(1),
            datetime.time(1, tzinfo=datetime.UTC),
            "takes only naive time keys",
        ),
    ],
)
def test_nan_nat_and_aware_keys_are_refused(
    key: object, good: object, bad: object, message: str
) -> None:
    """Refuse a nan, a NaT, an aware datetime and an aware time key, naming the field, and keep the value."""
    value = empty_value(key)
    value.add(good)
    with pytest.raises(ValueError, match=re.escape(f"Catalogue.keys {message}")):
        value.add(bad)
    assert list(value) == [good]


def test_member_and_optional_keys_compare_as_python_does() -> None:
    """Take -0.0 as 0.0 inside a record and an optional, find an absent optional, and ignore a datetime member's fold."""
    record = empty_value(Reading)
    record.add(Reading(1, -0.0, REPEATED.replace(fold=1)))
    assert Reading(1, 0.0, REPEATED) in record
    record.add(Reading(1, 0.0, REPEATED))
    assert len(record) == 1
    optional = empty_value(float | None)
    optional.update([-0.0, None])
    assert 0.0 in optional
    assert None in optional
    optional.add(0.0)
    assert len(optional) == 2
    with pytest.raises(ValueError, match="takes no nan keys"):
        optional.add(float("nan"))
    # An absent value stores a zero flags byte, which the naive check must not read.
    moment = empty_value(datetime.datetime | None)
    moment.add(None)
    assert None in moment
    moment.add(REPEATED)
    assert moment == {None, REPEATED}


def test_a_negative_zero_key_is_stored_as_zero() -> None:
    """Store -0.0 as 0.0, so a later 0.0 is the same key."""
    value = BoundedSet[float]([], capacity=4)
    value.add(-0.0)
    value.add(0.0)
    (key,) = value
    assert math.copysign(1, key) == 1


def test_a_naive_key_keeps_its_fold(unique_name: str) -> None:
    """Keep the first of two naive datetimes that differ only in fold, at its fold, through a write and a read."""
    with Times.create(unique_name, set()) as box:
        when = box.when
        when.add(REPEATED.replace(fold=1))
        when.add(REPEATED)
        assert len(when) == 1
        box.when = when
        (key,) = box.when
    assert key.fold == 1


def pack_int(n: int) -> bytes:
    return n.to_bytes(8, "little", signed=True)


def pack_float(x: float) -> bytes:
    return struct.pack("<d", x)


@pytest.mark.parametrize("size", [3, 12])
def test_forged_duplicates_and_nan_are_found_when_a_key_is_looked_up(
    size: int,
) -> None:
    """Refuse a forged nan and rewrite a forged -0.0 on the first lookup, and drop a forged duplicate on the second, by scan and by index."""
    rest = list(range(3, size))
    ints = forged(int, [pack_int(k) for k in [1, 2, 1, *rest]])
    assert 1 in ints
    assert len(ints) == size
    assert 1 in ints
    assert len(ints) == size - 1
    assert list(ints) == [1, 2, *rest]
    others = [float(k) for k in rest]
    nan = forged(float, [pack_float(x) for x in [math.nan, 1.0, *others]])
    with pytest.raises(ValueError, match="the stored value"):
        0.5 in nan  # noqa: B015
    zero = forged(float, [pack_float(x) for x in [-0.0, 1.0, *others]])
    assert 0.0 in zero
    assert math.copysign(1, next(iter(zero))) == 1


@pytest.mark.parametrize("size", [3, 40])
@pytest.mark.parametrize(("key", "pack"), [(int, pack_int), (float, pack_float)])
def test_a_first_change_after_a_read_handles_forged_copies(
    size: int, key: type, pack: collections.abc.Callable[[Any], bytes]
) -> None:
    """Remove every forged copy of a key on the first discard or remove after a read, and add no key it holds."""
    keys = [1, 2, *range(3, size), 1]
    discarded = forged(key, [pack(k) for k in keys])
    discarded.discard(1)
    assert 1 not in discarded
    assert len(discarded) == size - 2
    removed = forged(key, [pack(k) for k in keys])
    removed.remove(1)
    assert 1 not in removed
    with pytest.raises(KeyError):
        removed.remove(1)
    added = forged(key, [pack(k) for k in keys])
    added.add(2)
    assert sorted(added) == sorted(keys)


@pytest.mark.parametrize("size", [3, 12])
def test_a_pop_checks_forged_keys_first(size: int) -> None:
    """Drop a forged duplicate before a pop takes the last slot, and refuse a forged nan, by scan and by index."""
    rest = list(range(3, size))
    ints = forged(int, [pack_int(k) for k in [1, 2, *rest, 1]])
    assert ints.pop() == (rest[-1] if rest else 2)
    assert len(ints) == size - 2
    assert 1 in ints
    others = [float(k) for k in rest]
    nan = forged(float, [pack_float(x) for x in [1.0, *others, math.nan]])
    with pytest.raises(ValueError, match="the stored value"):
        nan.pop()
    assert len(nan) == size - 1


def test_a_stream_item_reused_for_a_shorter_string_finds_it(
    names: collections.abc.Callable[[str], str],
) -> None:
    """Find a short string in a stream item whose slot held a longer one."""
    with SharedStream.create(
        Annotated[BoundedSet[Annotated[str, Capacity(8)]], Capacity(4)],
        names("s"),
        capacity=2,
    ) as stream:
        reader = stream.reader(mode="latest")
        with stream.sender() as sender:
            # Three sends to two slots, so the last is encoded over the first.
            for text in ("abcdefgh", "abcdefgh", "ab"):
                sender.send({text})
        item = reader.receive(timeout=5)
    assert "ab" in item


def test_equality_follows_set_rules() -> None:
    """Compare equal to builtin sets and BoundedSets of the same keys in any slot order, and refuse what set refuses."""
    value = BoundedSet[int]([1, 2, 3], capacity=4)
    assert value == {1, 2, 3}
    assert value == frozenset({3, 2, 1})
    assert value == BoundedSet[int]([3, 1, 2], capacity=8)
    assert value != BoundedSet[int]([3, 1, 4], capacity=4)
    assert value != [1, 2, 3]
    assert BoundedSet[Red]([Red.A], capacity=2) != BoundedSet[Blue](
        [Blue.A], capacity=2
    )
    with pytest.raises(TypeError):
        hash(value)
    with pytest.raises(TypeError, match="never built"):
        value == NativeSet.__new__(BoundedSet)  # noqa: B015
    with pytest.raises(TypeError, match="never built"):
        value.update(NativeSet.__new__(BoundedSet))


def test_changing_the_size_while_iterating_raises() -> None:
    """Raise RuntimeError from an iterator whose set changed size."""
    value = BoundedSet[int]([1, 2], capacity=8)
    with pytest.raises(RuntimeError, match="Set changed size during iteration"):
        for key in value:
            value.add(key + 10)


@pytest.mark.parametrize("size", [8, 9, 10, 40])
def test_the_index_takes_over_at_nine_keys(size: int) -> None:
    """Find every key added and no other, before and after each removal, around the size the index starts at."""
    keys = [k * 7919 for k in range(size)]
    value = BoundedSet[int]([], capacity=64)
    for k in keys:
        value.add(k)
    assert all(k in value for k in keys)
    assert -1 not in value
    for i, k in enumerate(keys):
        value.remove(k)
        assert k not in value
        assert all(other in value for other in keys[i + 1 :])
        assert len(value) == size - i - 1


def test_a_value_that_is_no_key_is_not_in_the_set() -> None:
    """Treat a value that cannot be encoded as a key as absent from every BoundedSet."""
    value = BoundedSet[int]([1], capacity=2)
    assert "x" not in value  # type: ignore[comparison-overlap]
    value.discard("x")  # type: ignore[arg-type]
    with pytest.raises(KeyError) as raised:
        value.remove("x")  # type: ignore[arg-type]
    assert raised.value.args == ("x",)
    assert (BoundedSet[int]([1], capacity=2) == {1, "x"}) is False
    assert (BoundedSet[int]([1, 2], capacity=2) == {1, "x"}) is False
    assert value == {1}
    pair = BoundedSet[int]([1, 2], capacity=2)
    assert pair & {1, "x"} == {1}
    assert pair - {1, "x"} == {2}
    assert pair <= {1, 2, "x"}
    assert pair < {1, 2, "x"}
    assert not pair >= {1, 2, "x"}
    assert not pair > {1, "x"}
    assert pair.isdisjoint({"x"})


def test_two_threads_never_pass_the_capacity() -> None:
    """Keep the capacity of keys, and every key whose add returned, when two threads add at once."""
    value = BoundedSet[int]([], capacity=1000)
    added: list[list[int]] = [[], []]

    def fill(start: int) -> None:
        for k in range(start, 10**6, 2):
            try:
                value.add(k)
            except ValueError:
                return
            added[start].append(k)

    threads = [threading.Thread(target=fill, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(value) == 1000
    assert all(k in value for k in added[0] + added[1])


def test_a_pop_that_fails_to_decode_changes_nothing(unique_name: str) -> None:
    """Keep every key when the record pop would return raises as it is built."""
    shut = Gate(False)
    object.__setattr__(shut, "shut", True)
    with Gates.create(
        unique_name, BoundedSet[Gate]([Gate(False), shut], capacity=4)
    ) as box:
        gates = box.gates
        with pytest.raises(ValueError, match="the gate is shut"):
            gates.pop()
        assert len(gates) == 2
        gates.discard(Gate(False))
        assert len(gates) == 1


def test_set_methods_change_the_value() -> None:
    """Add, discard, remove, pop, clear and update keys as a set does, with set's errors."""
    value = BoundedSet[int]([1, 2], capacity=3)
    value.add(3)
    value.add(3)
    value.discard(9)
    value.remove(1)
    # The last slot fills the one removed, and pop takes the last slot.
    assert list(value) == [3, 2]
    assert value.pop() == 2
    value.clear()
    with pytest.raises(KeyError, match="pop from an empty set"):
        value.pop()
    with pytest.raises(KeyError):
        value.remove(1)
    value.update([1, 2], {3})
    with pytest.raises(
        ValueError, match="holds at most 3 elements; the value would have 4"
    ):
        value.update([1], [4, 5])
    assert list(value) == [1, 2, 3]


def test_operators_keep_the_class_and_capacity() -> None:
    """Return a Tags of the same capacity from each operator, with a builtin set on either side."""
    tags = Tags(["a", "b"], capacity=4)
    results: list[tuple[Any, set[str]]] = [
        (tags | {"c"}, {"a", "b", "c"}),
        ({"c"} | tags, {"a", "b", "c"}),
        (tags & {"a", "c"}, {"a"}),
        ({"a", "c"} & tags, {"a"}),
        (tags - {"a"}, {"b"}),
        ({"a", "c"} - tags, {"c"}),
        (tags ^ {"a", "c"}, {"b", "c"}),
        ({"a", "c"} ^ tags, {"b", "c"}),
    ]
    for result, expected in results:
        assert type(result) is Tags
        assert result.capacity == 4
        assert result == expected
    assert tags == {"a", "b"}


def test_an_operator_past_the_capacity_raises() -> None:
    """Raise ValueError from |, |= and ^= past the capacity, and leave the value as it was."""
    value = BoundedSet[int]([1, 2], capacity=2)
    with pytest.raises(ValueError, match="would have 3"):
        value | {3}
    with pytest.raises(ValueError, match="would have 3"):
        value |= {3}
    with pytest.raises(ValueError, match="would have 3"):
        value ^= {1, 3, 4}
    assert list(value) == [1, 2]


def test_operators_refuse_what_set_refuses() -> None:
    """Raise TypeError for an operand that is not a set, and for a BoundedSet that was never built."""
    value = BoundedSet[int]([1], capacity=2)
    with pytest.raises(TypeError):
        value | [1]  # type: ignore[operator]
    with pytest.raises(TypeError):
        value |= [1]  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        value <= [1]  # type: ignore[operator]  # noqa: B015
    with pytest.raises(TypeError, match="expects int, got str"):
        value | {"x"}  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="expects int, got str"):
        {"x"} - value
    unbuilt = NativeSet.__new__(BoundedSet)
    for op in (
        operator.or_,
        operator.and_,
        operator.sub,
        operator.xor,
        operator.ior,
        operator.iand,
        operator.isub,
        operator.ixor,
        operator.le,
        operator.lt,
        operator.ge,
        operator.gt,
        BoundedSet.isdisjoint,
    ):
        with pytest.raises(TypeError, match="never built"):
            op(value, unbuilt)
    assert value == {1}


def test_comparisons_follow_set_rules() -> None:
    """Order a BoundedSet against builtin sets and BoundedSets, either way round, as set orders sets."""
    sets: list[set[int]] = [set(), {1}, {1, 2}, {2, 3}, {1, 2, 3}]
    for a in sets:
        for b in sets:
            bounded_a = BoundedSet[int](a, capacity=4)
            bounded_b = BoundedSet[int](b, capacity=8)
            for left, right in ((bounded_a, b), (a, bounded_b), (bounded_a, bounded_b)):
                assert (left <= right) == (a <= b)
                assert (left < right) == (a < b)
                assert (left >= right) == (a >= b)
                assert (left > right) == (a > b)
            assert bounded_a.isdisjoint(b) == a.isdisjoint(b)
            assert bounded_a.isdisjoint(bounded_b) == a.isdisjoint(b)
            assert bounded_a.isdisjoint(iter(b)) == a.isdisjoint(b)


def test_a_value_is_its_own_operand() -> None:
    """Combine, compare and update a value with itself, which takes its mutex once at a time."""
    keys = set(range(12))
    value = BoundedSet[int](keys, capacity=16)
    value |= value
    value &= value
    value.update(value)
    assert value == keys
    assert value - value == set()
    assert value <= value  # noqa: PLR0124
    assert not value < value  # noqa: PLR0124
    value ^= value
    assert value == set()
