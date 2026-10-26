import collections.abc
import copy
import decimal
import gc
import math
import pickle
import re
import struct
import weakref
from dataclasses import dataclass
from typing import Annotated, Any

import numpy as np
import pytest
from crossproc import call_in_child, update_in_child

from sharedbox import (
    BoundedDict,
    BoundedList,
    BoundedSet,
    Capacity,
    SharedBox,
    SharedStream,
)
from sharedbox._layout import build_layout
from sharedbox._native import NativeDict

Name = Annotated[str, Capacity(16)]


class Catalogue(SharedBox):
    weights: Annotated[BoundedDict[int, float], Capacity(8)]


class Weights(BoundedDict[int, float]):
    __slots__ = ("tag",)
    tag: object


class Holder(BoundedDict[int, float]):
    pass


class Unreadable(BoundedDict[int, float]):
    __slots__ = ()

    def __iter__(self) -> collections.abc.Iterator[int]:
        raise AssertionError("a byte copy does not iterate the value")


@dataclass(frozen=True)
class Weighing:
    weights: Annotated[BoundedDict[int, float], Capacity(4)]


class DictPlaces(SharedBox):
    weighing: Weighing
    maybe: Annotated[BoundedDict[int, float], Capacity(4)] | None
    rows: Annotated[list[Annotated[BoundedDict[int, float], Capacity(2)]], Capacity(2)]


class Listed(SharedBox, identity="sbtest/bounded-dict/attach"):
    scores: Annotated[dict[Name, float], Capacity(8)]


class Bounded(SharedBox, identity="sbtest/bounded-dict/attach"):
    scores: Annotated[BoundedDict[Name, float], Capacity(8)]


class Copies(SharedBox):
    source: Annotated[Unreadable, Capacity(8)]
    target: Annotated[BoundedDict[int, float], Capacity(8)]
    wide: Annotated[BoundedDict[int, float], Capacity(16)]


class Counts(SharedBox):
    counts: Annotated[BoundedDict[int, int], Capacity(8)]


class Floats(SharedBox):
    values: Annotated[dict[int, float], Capacity(8)]


class Either(SharedBox):
    value: Annotated[BoundedDict[int, float], Capacity(4)] | Annotated[str, Capacity(8)]


class Labels(SharedBox):
    weights: Annotated[Weights, Capacity(8)]


class Holders(SharedBox):
    holder: Annotated[Holder, Capacity(4)]


def make(annotation: object, name: str = "T", field: str = "x") -> type:
    return type(
        name, (), {"__annotations__": {field: annotation}, "__module__": "__main__"}
    )


def pack_int(n: int) -> bytes:
    return n.to_bytes(8, "little", signed=True)


def pack_float(x: float) -> bytes:
    return struct.pack("<d", x)


def forged(items: list[tuple[int, float]]) -> Any:
    """Return a BoundedDict[int, float] read from stored slots holding `items`, as another program could write them."""
    types = build_layout(
        make(
            Annotated[BoundedDict[int, float], Capacity(max(16, len(items)))],
            "Catalogue",
            "weights",
        )
    ).types
    empty: Any = types.decode(0, bytes(64))
    head = bytearray(empty._stored())
    head[:4] = len(items).to_bytes(4, "little")
    slots = b"".join(pack_int(k) + pack_float(v) for k, v in items)
    return types.decode(0, bytes(head) + slots)


def test_a_bounded_dict_field_reads_back_its_values(unique_name: str) -> None:
    """Read a BoundedDict field back as a new BoundedDict holding the items written."""
    with Catalogue.create(unique_name, {1: 0.5, 2: 1.5}) as box:
        weights = box.weights
        assert isinstance(weights, BoundedDict)
        assert len(weights) == 2
        assert dict(weights) == {1: 0.5, 2: 1.5}
        read: object = weights
        assert not isinstance(read, collections.abc.MutableSet)
        assert not isinstance(read, collections.abc.MutableSequence)
        assert isinstance(read, collections.abc.MutableMapping)
        assert box.weights is not box.weights


def test_every_place_a_dict_goes_reads_back_as_bounded_dict(
    unique_name: str, names: collections.abc.Callable[[str], str]
) -> None:
    """Read a BoundedDict from a record member, a stream item, an optional, a list element and a union."""
    with (
        DictPlaces.create(
            unique_name + "-a",
            Weighing({1: 0.5}),  # type: ignore[arg-type]
            None,
            [{1: 1.0}, {2: 2.0, 3: 3.0}],
        ) as box,
        Either.create(unique_name + "-b", {4: 4.0}) as either,
    ):
        assert box.maybe is None
        box.maybe = {5: 5.0}  # type: ignore[assignment]
        values: list[Any] = [box.weighing.weights, box.maybe, *box.rows, either.value]
        with SharedStream.create(
            Annotated[BoundedDict[int, float], Capacity(4)], names("s"), capacity=2
        ) as stream:
            reader = stream.reader()
            with stream.sender() as sender:
                sender.send({9: 9.0})
            values.append(next(iter(reader)))
        either.value = "x"
        assert either.value == "x"
    assert all(type(v) is BoundedDict for v in values)
    assert [dict(v) for v in values] == [
        {1: 0.5},
        {5: 5.0},
        {1: 1.0},
        {2: 2.0, 3: 3.0},
        {4: 4.0},
        {9: 9.0},
    ]


def test_a_dict_written_over_an_older_one_stores_zero_bytes_after_its_keys(
    names: collections.abc.Callable[[str], str],
) -> None:
    """Store zero bytes past a dict key's end in a stream slot that held a longer one, so the key is found."""
    with SharedStream.create(
        Annotated[BoundedDict[Name, float], Capacity(1)], names("s"), capacity=2
    ) as stream:
        items = iter(stream.reader())
        with stream.sender() as sender:
            for text in ("a longer text", "a longer text", "a"):
                sender.send({text: 1.0})
                last = next(items)
    count_and_padding = (1).to_bytes(4, "little") + bytes(4)
    key = (1).to_bytes(4, "little") + b"a" + bytes(15)
    assert last._stored() == count_and_padding + key + bytes(4) + pack_float(1.0)
    assert last["a"] == 1.0


def test_dict_and_bounded_dict_declarations_attach_to_each_other(
    unique_name: str,
) -> None:
    """Update from a spawned process through a BoundedDict declaration a box made with dict."""
    with Listed.create(unique_name, {"a": 1.0}) as made:
        with Bounded.attach(unique_name) as attached:
            update_in_child(attached, scores={"b": 2.0, "c": 3.0})
        assert made.scores == {"b": 2.0, "c": 3.0}


def test_a_bounded_dict_write_copies_its_bytes(unique_name: str) -> None:
    """Copy the bytes of a BoundedDict of the field's types, and refuse one too large."""
    with Copies.create(
        unique_name, {3: 0.5, 1: 1.5}, {}, {k: float(k) for k in range(9)}
    ) as box:
        box.target = box.source
        assert box.target._stored() == box.source._stored()
        with pytest.raises(
            ValueError, match="holds at most 8 elements; the value has 9"
        ):
            box.target = box.wide
        assert dict(box.target) == {3: 0.5, 1: 1.5}


def test_a_bounded_dict_with_another_value_annotation_is_encoded_per_element(
    unique_name: str,
) -> None:
    """Encode a BoundedDict[int, int] written to a dict[int, float] field item by item, so it reads back floats."""
    with (
        Counts.create(unique_name + "-a", {1: 2, 3: 4}) as counts,
        Floats.create(unique_name + "-b", {}) as floats,
    ):
        floats.values = counts.counts  # type: ignore[assignment]
        assert floats.values == {1: 2.0, 3: 4.0}
        assert all(type(v) is float for v in floats.values.values())


def test_a_bounded_dict_that_was_never_built_is_refused(unique_name: str) -> None:
    """Refuse a BoundedDict made by the native __new__ alone, in a write and in ==, and keep the field's value."""
    with Catalogue.create(unique_name, {1: 0.5}) as box:
        unbuilt = NativeDict.__new__(BoundedDict)
        with pytest.raises(
            TypeError,
            match=r"Catalogue.weights got a BoundedDict that was never built",
        ):
            box.update(weights=unbuilt)
        with pytest.raises(TypeError, match="never built"):
            box.weights == unbuilt  # noqa: B015
        assert dict(box.weights) == {1: 0.5}


def test_iterating_a_bounded_dict_that_was_never_built_raises() -> None:
    """Raise TypeError when a BoundedDict made by the native __new__ alone is iterated."""
    with (
        pytest.raises(TypeError),
        pytest.warns(RuntimeWarning, match="uninitialized instance"),
    ):
        iter(NativeDict.__new__(BoundedDict))


@pytest.mark.parametrize(
    ("key", "message"),
    [
        (
            Annotated[decimal.Decimal, Capacity(8)],
            "BoundedDict keys cannot hold a Decimal",
        ),
        (int | float, "BoundedDict keys cannot hold a union of int:8 and float:8"),
        (
            Annotated[frozenset[int], Capacity(4)],
            "BoundedDict keys cannot hold a frozenset",
        ),
        (
            Annotated[BoundedSet[int], Capacity(2)],
            "dict keys of set[2](int:8) are not hashable when read back",
        ),
        (
            Annotated[BoundedDict[int, float], Capacity(2)],
            "dict keys of dict[2](int:8,float:8) are not hashable when read back",
        ),
        (
            Annotated[BoundedList[int], Capacity(2)],
            "dict keys of list[2](int:8) are not hashable when read back",
        ),
        (
            Annotated[list[int], Capacity(2)],
            "dict keys of list[2](int:8) are not hashable when read back",
        ),
        (
            Annotated[set[int], Capacity(2)],
            "dict keys of set[2](int:8) are not hashable when read back",
        ),
        (
            Annotated[dict[int, float], Capacity(2)],
            "dict keys of dict[2](int:8,float:8) are not hashable when read back",
        ),
    ],
)
def test_keys_that_compare_unlike_their_bytes_are_refused(
    key: object, message: str
) -> None:
    """Refuse a BoundedDict key type whose equal values may differ in bytes or are not hashable, and take it as a value type."""
    with pytest.raises(TypeError, match=re.escape(f"Box.keys: {message}")):
        build_layout(
            make(Annotated[BoundedDict[key, float], Capacity(4)], "Box", "keys")  # type: ignore[valid-type]
        )
    build_layout(
        make(Annotated[BoundedDict[int, key], Capacity(4)], "Box", "keys")  # type: ignore[valid-type]
    )


def test_a_pickled_bounded_dict_reads_back_in_a_spawned_process(
    unique_name: str,
) -> None:
    """Read back a pickled BoundedDict in a spawned process, and keep a subclass and its slots through pickle and copies."""
    with (
        Catalogue.create(unique_name + "-a", {1: 0.5, 2: 1.5}) as box,
        Labels.create(unique_name + "-b", {3: 3.5}) as labels,
    ):
        value = box.weights
        weights = labels.weights
    assert call_in_child(dict, value) == {1: 0.5, 2: 1.5}
    weights.tag = "kept"
    for again in (
        pickle.loads(pickle.dumps(weights)),
        copy.copy(weights),
        copy.deepcopy(weights),
    ):
        assert type(again) is Weights
        assert again.tag == "kept"
        assert dict(again) == {3: 3.5}


def test_a_stored_count_past_the_capacity_reads_as_corrupt() -> None:
    """Raise ValueError naming the field when a dict's stored count passes its capacity."""
    layout = build_layout(make(Annotated[BoundedDict[int, float], Capacity(8)]))
    data = (9).to_bytes(4, "little") + bytes(4 + 16 * 8)
    with pytest.raises(ValueError, match="T.x: the stored value"):
        layout.types.decode(0, data)


def test_a_cycle_through_a_value_or_its_iterator_is_collected(
    unique_name: str,
) -> None:
    """Free a value that holds itself, and one that holds an iterator over itself."""
    with Holders.create(unique_name, {1: 1.0}) as box:
        holder, iterated = box.holder, box.holder
    holder.me = holder  # type: ignore[attr-defined]
    iterated.it = iter(iterated)  # type: ignore[attr-defined]
    refs = [weakref.ref(holder), weakref.ref(iterated)]
    del holder, iterated
    gc.collect()
    assert [ref() for ref in refs] == [None, None]


def test_lookups_follow_dict_rules(unique_name: str) -> None:
    """Look keys up as a dict does: missing keys, a key of another type, get's default and the item order."""
    with Catalogue.create(unique_name, {2: 1.5, 1: 0.5, 3: 2.5}) as box:
        weights = box.weights
    assert weights[1] == 0.5
    assert weights.get(3) == 2.5
    assert weights.get(9) is None
    assert weights.get(9, 7) == 7
    assert 9 not in weights
    assert "x" not in weights  # type: ignore[comparison-overlap]
    with pytest.raises(KeyError) as missing:
        weights[9]
    assert missing.value.args == (9,)
    assert list(weights.items()) == [(2, 1.5), (1, 0.5), (3, 2.5)]
    assert list(weights.keys()) == [2, 1, 3]
    assert list(weights.values()) == [1.5, 0.5, 2.5]
    assert weights == {1: 0.5, 2: 1.5, 3: 2.5}
    assert weights != {1: 0.5, 2: 1.5, 3: 9.0}
    assert weights != {1: 0.5, 2: 1.5}
    assert weights != {1: 0.5, 2: 1.5, "x": 2.5}
    assert weights == weights.copy()
    assert weights.__eq__([1, 2, 3]) is NotImplemented


@pytest.mark.parametrize("size", [3, 40])
def test_forged_duplicate_keys_keep_the_last_value(size: int) -> None:
    """Read a forged duplicate key's last value by scan and by index, and keep the first key's position once the keys are checked."""
    rest = [(k, float(k)) for k in range(3, size)]
    items = [(1, 0.5), (2, 1.0), (1, 9.0), *rest]
    weights = forged(items)
    assert weights[1] == 9.0
    for _ in range(64):
        assert weights.get(2) == 1.0
    assert weights[1] == 9.0
    assert list(weights.items()) == [(1, 9.0), (2, 1.0), *rest]
    assert forged(items) == {1: 9.0, 2: 1.0, **dict(rest)}


Pair = Annotated[BoundedDict[int, float], Capacity(2)]


class Nested(SharedBox):
    bounded_lists: Annotated[
        BoundedDict[int, Annotated[BoundedList[int], Capacity(2)]], Capacity(2)
    ]
    bounded_sets: Annotated[
        BoundedDict[int, Annotated[BoundedSet[int], Capacity(2)]], Capacity(2)
    ]
    bounded_dicts: Annotated[BoundedDict[int, Pair], Capacity(2)]
    lists: Annotated[BoundedDict[int, Annotated[list[int], Capacity(2)]], Capacity(2)]
    sets: Annotated[BoundedDict[int, Annotated[set[int], Capacity(2)]], Capacity(2)]
    dicts: Annotated[
        BoundedDict[int, Annotated[dict[int, float], Capacity(2)]], Capacity(2)
    ]
    in_dict: Annotated[dict[int, Pair], Capacity(2)]
    in_list: Annotated[BoundedList[Pair], Capacity(2)]


class ListedSets(SharedBox, identity="sbtest/bounded-dict/nested-attach"):
    groups: Annotated[dict[int, Annotated[BoundedSet[int], Capacity(2)]], Capacity(2)]


class BoundedSets(SharedBox, identity="sbtest/bounded-dict/nested-attach"):
    groups: Annotated[
        BoundedDict[int, Annotated[BoundedSet[int], Capacity(2)]], Capacity(2)
    ]


def test_a_bounded_dict_nests_with_other_collections(unique_name: str) -> None:
    """Write and read back a BoundedDict holding each kind of collection, held by a dict and a BoundedList, and attach dict and BoundedDict declarations of nested sets to each other."""
    written: dict[str, Any] = {
        "bounded_lists": {1: [1, 2]},
        "bounded_sets": {1: {1, 2}},
        "bounded_dicts": {1: {1: 0.5}},
        "lists": {1: [1, 2]},
        "sets": {1: {1, 2}},
        "dicts": {1: {1: 0.5}},
        "in_dict": {1: {2: 1.5}},
        "in_list": [{2: 1.5}, {}],
    }
    classes = {
        "bounded_lists": BoundedList,
        "bounded_sets": BoundedSet,
        "bounded_dicts": BoundedDict,
        "lists": list,
        "sets": set,
        "dicts": dict,
        "in_dict": BoundedDict,
        "in_list": BoundedDict,
    }
    with Nested.create(
        unique_name + "-a", **{n: type(v)() for n, v in written.items()}
    ) as box:
        for name, value in written.items():
            setattr(box, name, value)
            read = getattr(box, name)
            assert read == value
            inner = read.values() if isinstance(read, collections.abc.Mapping) else read
            assert all(type(v) is classes[name] for v in inner)
            setattr(box, name, read)
            assert getattr(box, name) == value
    with (
        ListedSets.create(unique_name + "-b", {}) as made,
        BoundedSets.attach(unique_name + "-b") as attached,
    ):
        attached.groups = {1: {1, 2}}  # type: ignore[assignment]
        assert made.groups == {1: {1, 2}}
        assert type(made.groups[1]) is BoundedSet
        made.groups = {2: {3}}  # type: ignore[dict-item]
        assert attached.groups == {2: {3}}


def test_a_bounded_dict_reverses_and_converts_as_a_dict(unique_name: str) -> None:
    """Reverse a BoundedDict over its keys, and have numpy take it as it takes a dict, not as a sequence, before and after a change."""
    with Catalogue.create(unique_name, {0: 0.5, 1: 1.5, 5: 2.5}) as box:
        weights = box.weights
    assert list(reversed(weights)) == [5, 1, 0]
    assert np.array(weights).shape == np.array(dict(weights)).shape == ()
    weights[7] = 3.5
    del weights[0]
    assert list(reversed(weights)) == [7, 5, 1]
    assert np.array(weights).shape == ()


def test_a_bounded_dict_without_its_types_is_refused() -> None:
    """Refuse a BoundedDict field declared without its key and value types, showing how to give them."""
    with pytest.raises(
        TypeError,
        match=re.escape(
            "T.x: BoundedDict needs its key and value types, as in"
            " Annotated[BoundedDict[int, float], Capacity(n)]"
        ),
    ):
        build_layout(make(Annotated[BoundedDict, Capacity(4)]))


@dataclass(frozen=True)
class Gate:
    shut: bool

    def __post_init__(self) -> None:
        if self.shut:
            raise ValueError("the gate is shut")


def test_a_bounded_dict_is_built_outside_a_box() -> None:
    """Build a BoundedDict from a mapping or pairs, and refuse one past its capacity, of the wrong types or without its types."""
    assert BoundedDict[int, float]({1: 0.5}, capacity=8) == {1: 0.5}
    pairs = BoundedDict[int, float]([(2, 1.5), (1, 0.5), (2, 2.5)], capacity=2)
    assert list(pairs.items()) == [(2, 2.5), (1, 0.5)]
    assert BoundedDict[int, float](capacity=4) == {}
    with pytest.raises(
        ValueError,
        match=re.escape(
            "BoundedDict[int, float] holds at most 1 elements; the value would have 2"
        ),
    ):
        BoundedDict[int, float]({1: 0.5, 2: 1.5}, capacity=1)
    with pytest.raises(
        TypeError, match=re.escape("BoundedDict[int, float] expects int, got str")
    ):
        BoundedDict[int, float]({"x": 0.5}, capacity=4)  # type: ignore[arg-type]
    with pytest.raises(
        TypeError, match=re.escape("BoundedDict[int, float](values, capacity=n)")
    ):
        BoundedDict({1: 0.5}, capacity=4)


def test_repr_shows_the_types_and_capacity() -> None:
    """Show the key and value types, the items in insertion order and the capacity, with a subclass's own name."""
    assert (
        repr(BoundedDict[int, float]({1: 0.5}, capacity=8))
        == "BoundedDict[int, float]({1: 0.5}, capacity=8)"
    )
    assert (
        repr(Holder({2: 1.0, 1: 0.5}, capacity=4))
        == "Holder[int, float]({2: 1.0, 1: 0.5}, capacity=4)"
    )


def test_dict_methods_change_the_value() -> None:
    """Set, delete, pop, pop items, set defaults, update and clear as a dict does, with dict's errors."""
    weights = BoundedDict[int, float]({1: 0.5, 2: 1.5}, capacity=4)
    weights[3] = 2.5
    del weights[1]
    with pytest.raises(KeyError) as missing:
        del weights[1]
    assert missing.value.args == (1,)
    assert weights.pop(2) == 1.5
    assert weights.pop(2, 7) == 7
    with pytest.raises(KeyError) as missing:
        weights.pop(2)
    assert missing.value.args == (2,)
    assert weights.setdefault(3, 9.0) == 2.5
    assert weights.setdefault(4, 4.5) == 4.5
    weights.update({5: 5.5})
    weights.update([(6, 6.5)])
    assert list(weights.items()) == [(3, 2.5), (4, 4.5), (5, 5.5), (6, 6.5)]
    assert weights.popitem() == (6, 6.5)
    weights.clear()
    assert weights == {}
    with pytest.raises(KeyError, match=re.escape("popitem(): dictionary is empty")):
        weights.popitem()
    with pytest.raises(TypeError, match="expects int, got str"):
        weights["x"] = 1.0  # type: ignore[index]
    with pytest.raises(KeyError):
        del weights["x"]  # type: ignore[arg-type]
    assert weights.pop("x", 7) == 7  # type: ignore[call-overload]
    with pytest.raises(TypeError, match="expects float, got NoneType"):
        weights.setdefault(1)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="has length 3; 2 is required"):
        weights.update([(1, 0.5, 2)])  # type: ignore[list-item]
    with pytest.raises(TypeError, match="update expected at most 1 argument"):
        weights.update({}, {})  # type: ignore[call-overload]
    assert weights == {}
    scores = BoundedDict[Name, float]({"a": 1.0}, capacity=4)
    scores.update({"b": 2.0}, c=3.0, a=0.5)
    assert list(scores.items()) == [("a", 0.5), ("b", 2.0), ("c", 3.0)]


def test_merging_keeps_the_class_and_capacity() -> None:
    """Return a Holder of the same capacity from | with a dict on either side, as dict orders the items."""
    holder = Holder({1: 0.5, 2: 1.5}, capacity=4)
    results: list[tuple[Any, list[tuple[int, float]]]] = [
        (holder | {3: 2.5, 1: 9.0}, [(1, 9.0), (2, 1.5), (3, 2.5)]),
        ({3: 2.5, 1: 9.0} | holder, [(3, 2.5), (1, 0.5), (2, 1.5)]),
        (
            holder | BoundedDict[int, float]({4: 4.5}, capacity=8),
            [(1, 0.5), (2, 1.5), (4, 4.5)],
        ),
    ]
    for result, expected in results:
        assert type(result) is Holder
        assert result.capacity == 4
        assert list(result.items()) == expected
    holder |= [(3, 3.5)]
    assert list(holder.items()) == [(1, 0.5), (2, 1.5), (3, 3.5)]
    with pytest.raises(TypeError):
        holder | [(4, 1.0)]  # type: ignore[operator]
    with pytest.raises(TypeError):
        [(4, 1.0)] | holder  # type: ignore[operator]


def test_mutations_refuse_a_value_that_was_never_built() -> None:
    """Raise TypeError from update, | and |= with a BoundedDict that was never built, and keep the value."""
    weights = BoundedDict[int, float]({1: 0.5}, capacity=4)
    unbuilt = NativeDict.__new__(BoundedDict)
    with pytest.raises(TypeError, match="never built"):
        weights.update(unbuilt)
    with pytest.raises(TypeError, match="never built"):
        weights | unbuilt
    with pytest.raises(TypeError, match="never built"):
        weights |= unbuilt
    assert weights == {1: 0.5}


def test_deleting_keeps_the_order_across_the_index() -> None:
    """Keep insertion order and find every remaining key while 9 keys are deleted one at a time from the middle."""
    keys = [k * 7919 for k in range(9)]
    weights = BoundedDict[int, float]({k: float(k) for k in keys}, capacity=16)
    left = list(keys)
    while left:
        assert all(weights[k] == float(k) for k in left)
        key = left.pop(len(left) // 2)
        del weights[key]
        assert key not in weights
        assert list(weights) == left
        assert all(k in weights for k in left)


def test_a_nan_value_does_not_hide_its_key() -> None:
    """Find a key whose value is nan, and compare unequal to a copy, as a dict holding a fresh nan does."""
    weights = BoundedDict[int, float]({2: 1.5}, capacity=4)
    weights[1] = math.nan
    assert 1 in weights
    assert math.isnan(weights[1])
    assert weights != weights.copy()


def test_replacing_a_value_keeps_the_position() -> None:
    """Keep a key where it was when its value is replaced."""
    weights = BoundedDict[int, float]({1: 0.5, 2: 1.5}, capacity=4)
    weights[1] = 3.0
    assert list(weights.items()) == [(1, 3.0), (2, 1.5)]


def test_a_mutation_past_the_capacity_names_the_lengths() -> None:
    """Raise ValueError from a set, a default, an update and |= of a new key at a full capacity, keep the value, and replace a value."""
    weights = BoundedDict[int, float]({1: 0.5, 2: 1.5}, capacity=2)
    with pytest.raises(
        ValueError, match="holds at most 2 elements; the value would have 3"
    ):
        weights[3] = 1.0
    with pytest.raises(ValueError, match="would have 3"):
        weights.setdefault(3, 1.0)
    with pytest.raises(ValueError, match="would have 3"):
        weights.update({1: 9.0, 3: 1.0})
    with pytest.raises(ValueError, match="would have 3"):
        weights |= {2: 9.0, 3: 1.0}
    assert list(weights.items()) == [(1, 0.5), (2, 1.5)]
    weights[1] = 3.0
    weights.update({2: 4.0})
    assert list(weights.items()) == [(1, 3.0), (2, 4.0)]


def test_merging_in_place_past_the_index_size() -> None:
    """Merge in place more keys than a lookup scans, with replacements, in insertion order."""
    weights = BoundedDict[int, float](dict.fromkeys(range(10), 0.0), capacity=32)
    weights |= dict.fromkeys(range(5, 20), 1.0)
    expected = [(k, 0.0 if k < 5 else 1.0) for k in range(20)]
    assert list(weights.items()) == expected
    assert all(weights[k] == v for k, v in expected)


def test_a_value_merges_with_itself() -> None:
    """Merge and update a value with itself, which takes its mutex once at a time, and leave it unchanged."""
    items = {k: float(k) for k in range(12)}
    weights = BoundedDict[int, float](items, capacity=16)
    weights |= weights
    weights.update(weights)
    assert list(weights.items()) == list(items.items())
    assert weights | weights == items


def test_a_popitem_that_fails_to_decode_changes_nothing() -> None:
    """Keep every item when the value pop or popitem would return raises as it is built."""
    shut = Gate(False)
    object.__setattr__(shut, "shut", True)
    gates = BoundedDict[int, Gate]({1: Gate(False), 2: shut}, capacity=4)
    with pytest.raises(ValueError, match="the gate is shut"):
        gates.pop(2)
    with pytest.raises(ValueError, match="the gate is shut"):
        gates.popitem()
    assert list(gates) == [1, 2]
    del gates[2]
    assert gates.popitem() == (1, Gate(False))


def test_changing_the_size_while_iterating_raises() -> None:
    """Raise RuntimeError from an iterator whose dict changed size."""
    weights = BoundedDict[int, float]({1: 0.5, 2: 1.5}, capacity=8)
    with pytest.raises(RuntimeError, match="dictionary changed size during iteration"):
        for key in weights:
            weights[key + 10] = 1.0


@pytest.mark.parametrize("size", [3, 40])
def test_a_first_deletion_after_a_read_removes_forged_copies(size: int) -> None:
    """Remove every forged copy of a key on the first del or pop after a read, keeping the order of the rest, and replace the value a lookup finds."""
    rest = [(k, float(k)) for k in range(3, size)]
    items = [(1, 0.5), (2, 1.0), *rest, (1, 9.0)]
    deleted = forged(items)
    del deleted[1]
    assert 1 not in deleted
    assert list(deleted.items()) == [(2, 1.0), *rest]
    popped = forged(items)
    assert popped.pop(1) == 9.0
    assert 1 not in popped
    assert list(popped.items()) == [(2, 1.0), *rest]
    replaced = forged(items)
    replaced[1] = 5.0
    assert replaced == {1: 5.0, 2: 1.0, **dict(rest)}
    assert list(replaced.items()) == [(1, 5.0), (2, 1.0), *rest]
