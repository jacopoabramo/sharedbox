import collections.abc
import copy
import enum
import pickle
import queue
import threading
from dataclasses import dataclass, make_dataclass
from typing import Annotated, Any, TypeVar, get_origin

import pytest
from crossproc import call_in_child, update_in_child

from sharedbox import BoundedList, Capacity, SharedBox, SharedStream, field
from sharedbox._layout import build_layout
from sharedbox._native import NativeCollection

T = TypeVar("T")


class Scan(SharedBox):
    points: Annotated[BoundedList[float], Capacity(8)]


class MyList(BoundedList[T]):
    pass


class Points(BoundedList[float]):
    __slots__ = ()


class Tagged(BoundedList[float]):
    __slots__ = ("tag",)
    tag: object


class Labelled(BoundedList[T]):
    def __init__(
        self, values: collections.abc.Iterable[T] = (), *, capacity: int, tag: str
    ) -> None:
        self.tag = tag


class LabelledPoints(Labelled[float]):
    pass


class Unreadable(BoundedList[float]):
    __slots__ = ()

    def __iter__(self) -> collections.abc.Iterator[float]:
        raise AssertionError("a byte copy does not iterate the value")


@dataclass(frozen=True)
class Shot:
    values: Annotated[BoundedList[int], Capacity(4)]


class Roomy(SharedBox):
    points: Annotated[BoundedList[float], Capacity(16)]


class Floats(SharedBox):
    points: Annotated[list[float], Capacity(8)]


class Whole(SharedBox):
    points: Annotated[BoundedList[int], Capacity(8)]


class Listed(SharedBox, identity="sbtest/bounded/attach"):
    points: Annotated[list[float], Capacity(8)]


class Bounded(SharedBox, identity="sbtest/bounded/attach"):
    points: Annotated[BoundedList[float], Capacity(8)]


class Red(enum.Enum):
    r = 1


class Blue(enum.Enum):
    r = 1


@dataclass(frozen=True)
class Left:
    x: float


@dataclass(frozen=True)
class Right:
    x: float


class Reds(SharedBox):
    v: Annotated[BoundedList[Red], Capacity(4)]


class Blues(SharedBox):
    v: Annotated[BoundedList[Blue], Capacity(4)]


class Lefts(SharedBox):
    v: Annotated[BoundedList[Left], Capacity(4)]


class Rights(SharedBox):
    v: Annotated[BoundedList[Right], Capacity(4)]


class Places(SharedBox):
    shot: Shot
    maybe: Annotated[BoundedList[int], Capacity(4)] | None
    rows: Annotated[list[Annotated[BoundedList[int], Capacity(2)]], Capacity(2)]
    points: BoundedList[int] = field(capacity=4)


@dataclass
class Point:
    x: float
    y: float


@dataclass
class Gate:
    shut: bool

    def __post_init__(self) -> None:
        if self.shut:
            raise ValueError("the gate is shut")


class Gates(SharedBox):
    gates: Annotated[BoundedList[Gate], Capacity(4)]


class Trace(SharedBox):
    points: Annotated[BoundedList[Point], Capacity(4)]


class Short(SharedBox):
    points: Annotated[BoundedList[float], Capacity(3)]


class Counts(SharedBox):
    values: Annotated[BoundedList[int], Capacity(1000)]


def make(annotation: object) -> type:
    return type(
        "T", (), {"__annotations__": {"x": annotation}, "__module__": "__main__"}
    )


def test_a_bounded_list_field_reads_back_its_values(unique_name: str) -> None:
    """Read a BoundedList field back as a BoundedList holding the values written."""
    with Scan.create(unique_name, [1.0, 2.5]) as box:
        points = box.points
        assert isinstance(points, BoundedList)
        assert issubclass(type(points), collections.abc.MutableSequence)
        assert len(points) == 2
        assert points[1] == 2.5
        assert points[-1] == 2.5
        assert list(points) == [1.0, 2.5]


def test_an_index_out_of_range_raises_index_error(unique_name: str) -> None:
    """Raise IndexError for an index past either end."""
    with Scan.create(unique_name, [1.0, 2.5]) as box:
        points = box.points
        with pytest.raises(IndexError):
            points[2]
        with pytest.raises(IndexError):
            points[-3]
        with pytest.raises(IndexError):
            points[2**70]


def test_setting_an_element_checks_it_as_a_write_would(unique_name: str) -> None:
    """Refuse an element of the wrong type with the field's message, and change only the value read."""
    with Scan.create(unique_name, [1.0, 2.5]) as box:
        points = box.points
        with pytest.raises(TypeError, match="Scan.points expects float, got str"):
            points[0] = "x"  # type: ignore[call-overload]
        points[0] = 3.0
        assert points[0] == 3.0
        assert box.points[0] == 1.0


def test_a_bounded_list_outlives_its_box(unique_name: str) -> None:
    """Keep the values of a BoundedList read from a box after the box is closed and unlinked."""
    box = Scan.create(unique_name, [1.0, 2.5])
    points = box.points
    box.close()
    Scan.unlink(unique_name)
    assert list(points) == [1.0, 2.5]


def test_every_place_a_list_goes_reads_back_as_bounded_list(
    unique_name: str, names: collections.abc.Callable[[str], str]
) -> None:
    """Read a BoundedList from a record member, a stream item, an optional, a list element, a snapshot, an event and a watch."""
    seen: queue.Queue[object] = queue.Queue()
    with Places.create(
        unique_name,
        Shot([1, 2]),  # type: ignore[arg-type]
        None,
        [[1], [2, 3]],
        [4],
    ) as box:
        assert box.maybe is None
        box.maybe = [5, 6]  # type: ignore[assignment]
        values = [
            box.shot.values,
            box.maybe,
            *box.rows,
            box.points,
            box.snapshot()["points"],
        ]
        assert type(box.rows) is list
        box.events.points.connect(seen.put)
        future = box.watch("points").future()
        box.points = [7, 8]  # type: ignore[assignment]
        values += [seen.get(timeout=5), future.result(5)]
        assert box.points is not box.points
        with SharedStream.create(
            Annotated[BoundedList[int], Capacity(4)], names("s"), capacity=2
        ) as stream:
            reader = stream.reader()
            with stream.sender() as sender:
                sender.send([9, 10])
            values.append(next(iter(reader)))
    assert all(type(v) is BoundedList for v in values)
    assert [list(v) for v in values] == [
        [1, 2],
        [5, 6],
        [1],
        [2, 3],
        [4],
        [4],
        [7, 8],
        [7, 8],
        [9, 10],
    ]


@pytest.mark.parametrize(
    ("annotation", "before"),
    [
        (Annotated[BoundedList[float], Capacity(8)], b""),
        (Annotated[BoundedList[float], Capacity(8)] | None, b"\x01" + bytes(7)),
    ],
)
def test_a_stored_count_past_the_capacity_reads_as_corrupt(
    annotation: object, before: bytes
) -> None:
    """Raise ValueError naming the field when a list's stored count, alone or inside an optional, passes its capacity."""
    layout = build_layout(make(annotation))
    data = before + (9).to_bytes(4, "little") + bytes(4 + 8 * 8)
    with pytest.raises(ValueError, match="T.x: the stored value"):
        layout.types.decode(0, data)


def test_a_mutation_past_the_capacity_names_the_lengths(unique_name: str) -> None:
    """Raise ValueError naming the capacity and the length an append would give."""
    with Short.create(unique_name, [1.0, 2.0, 3.0]) as box:
        points = box.points
        with pytest.raises(
            ValueError, match="holds at most 3 elements; the value would have 4"
        ):
            points.append(4.0)
        assert list(points) == [1.0, 2.0, 3.0]


def test_extend_from_a_generator_past_the_capacity_changes_nothing(
    unique_name: str,
) -> None:
    """Leave the value as it was when a generator passes the capacity part way through."""
    with Short.create(unique_name, [1.0]) as box:
        points = box.points
        with pytest.raises(ValueError, match="Short.points holds at most 3 elements"):
            points.extend(x for x in [1.0] * 10)
        assert len(points) == 1


def test_equality_follows_list_rules(unique_name: str) -> None:
    """Compare equal to a list or BoundedList of equal elements, never to a tuple, and refuse hashing."""
    with Scan.create(unique_name, [1.0, -0.0]) as box:
        points = box.points
        assert points == [1.0, 0.0]
        assert [1.0, 0.0] == points
        assert points == box.points
        assert points != (1.0, 0.0)
        assert points != [1.0]
        assert points != [1.0, 2.0]
        box.points = [float("nan")]  # type: ignore[assignment]
        assert box.points != [float("nan")]
        assert box.points != box.points
        with pytest.raises(TypeError):
            hash(points)


def test_a_decoded_element_is_a_copy(unique_name: str) -> None:
    """Leave the stored element as it was when the record an index returned is changed."""
    with Trace.create(unique_name, [Point(1.0, 2.0)]) as box:
        points = box.points
        points[0].x = 5.0
        assert points[0].x == 1.0


def test_two_threads_never_pass_the_capacity(unique_name: str) -> None:
    """Stop two threads appending to one value at its capacity, with one element per append that returned."""
    with Counts.create(unique_name, []) as box:
        values = box.values
    appended = [0, 0]

    def fill(which: int) -> None:
        while True:
            try:
                values.append(which)
            except ValueError:
                return
            appended[which] += 1

    threads = [threading.Thread(target=fill, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(values) == 1000
    assert sum(appended) == 1000
    assert values.count(0) == appended[0]


def test_a_pop_that_fails_to_decode_changes_nothing(unique_name: str) -> None:
    """Keep every element when the record pop would return raises as it is built."""
    shut = Gate(False)
    shut.shut = True
    with Gates.create(unique_name, [Gate(False), shut]) as box:
        gates = box.gates
        with pytest.raises(ValueError, match="the gate is shut"):
            gates.pop()
        assert len(gates) == 2
        assert gates[0] == Gate(False)
        with pytest.raises(ValueError, match="the gate is shut"):
            gates[1]


def test_an_extended_slice_takes_as_many_elements_as_it_covers(
    unique_name: str,
) -> None:
    """Refuse an extended slice assignment of another length with list's message."""
    with Scan.create(unique_name, [1.0, 2.0, 3.0]) as box:
        points = box.points
        with pytest.raises(
            ValueError,
            match="attempt to assign sequence of size 1 to extended slice of size 2",
        ):
            points[::2] = [9.0]
        assert list(points) == [1.0, 2.0, 3.0]


def test_a_smaller_bounded_list_fits_a_larger_field(unique_name: str) -> None:
    """Write a value read from one field to a roomier field, and refuse one that passes the capacity."""
    with (
        Scan.create(unique_name + "-a", [1.0, 2.0, 3.0]) as small,
        Roomy.create(unique_name + "-b", []) as large,
    ):
        large.points = small.points
        assert large.points == [1.0, 2.0, 3.0]
        large.update(points=[float(i) for i in range(9)])
        with pytest.raises(
            ValueError, match="holds at most 8 elements; the value has 9"
        ):
            small.points = large.points
        assert small.points == [1.0, 2.0, 3.0]


def test_a_bounded_list_of_another_element_type_is_encoded_per_element(
    unique_name: str,
) -> None:
    """Store the floats of an int BoundedList written to a list[float] field."""
    with (
        Whole.create(unique_name + "-a", [1, 2]) as ints,
        Floats.create(unique_name + "-b", []) as floats,
    ):
        floats.update(points=ints.points)
        assert floats.points == [1.0, 2.0]
        assert all(type(p) is float for p in floats.points)


def test_list_and_bounded_list_declarations_attach_to_each_other(
    unique_name: str,
) -> None:
    """Update from a spawned process through a BoundedList declaration a box made with list."""
    with Listed.create(unique_name, [1.0]) as made:
        with Bounded.attach(unique_name) as attached:
            update_in_child(attached, points=[2.0, 3.0])
        assert made.points == [2.0, 3.0]


@pytest.mark.parametrize(
    ("source", "target", "first", "second"),
    [(Reds, Blues, Red.r, Blue.r), (Lefts, Rights, Left(1.0), Right(2.0))],
)
def test_a_bounded_list_of_another_class_is_refused_as_a_list_is(
    unique_name: str,
    source: type[SharedBox],
    target: type[SharedBox],
    first: object,
    second: object,
) -> None:
    """Refuse a BoundedList whose elements are of a same-shaped class and keep the field's value."""
    with (
        source.create(unique_name + "-a", [first]) as one,
        target.create(unique_name + "-b", [second]) as other,
    ):
        with pytest.raises(TypeError) as listed:
            other.update(v=[first])
        with pytest.raises(TypeError) as bounded:
            other.update(v=one.snapshot()["v"])
        assert str(bounded.value) == str(listed.value)
        assert other.snapshot()["v"] == [second]


def test_a_bounded_list_that_was_never_built_is_refused(unique_name: str) -> None:
    """Refuse a BoundedList made by the native __new__ alone and keep the field's value."""
    with Scan.create(unique_name, [1.0]) as box:
        with pytest.raises(
            TypeError, match=r"Scan.points got a BoundedList that was never built"
        ):
            box.update(points=NativeCollection.__new__(BoundedList))
        assert box.points == [1.0]


def test_a_bounded_list_is_built_outside_a_box() -> None:
    """Build a BoundedList from values and refuse one past its capacity or of the wrong element type."""
    assert BoundedList[float]([1.0, 2.0], capacity=4) == [1.0, 2.0]
    assert BoundedList[float](capacity=4) == []
    with pytest.raises(ValueError, match=r"BoundedList\[float\] holds at most 4"):
        BoundedList[float]([1.0] * 5, capacity=4)
    with pytest.raises(TypeError, match=r"BoundedList\[float\] expects float"):
        BoundedList[float](["x"], capacity=4)  # type: ignore[list-item]
    with pytest.raises(TypeError, match=r"BoundedList\[float\]\(values, capacity=n\)"):
        BoundedList([1.0], capacity=4)


def test_repr_shows_the_element_type_and_capacity(unique_name: str) -> None:
    """Show the element type, the elements and the capacity, for a value built or read."""
    assert (
        repr(BoundedList[float]([1.0, 2.0], capacity=4))
        == "BoundedList[float]([1.0, 2.0], capacity=4)"
    )
    with Scan.create(unique_name, [1.5]) as box:
        assert repr(box.points) == "BoundedList[float]([1.5], capacity=8)"


@pytest.mark.parametrize(
    "make_copy",
    [
        copy.copy,
        copy.deepcopy,
        lambda bl: bl.copy(),
        lambda bl: bl[:],
        lambda bl: bl + [3.0],
        lambda bl: bl * 2,
        lambda bl: 2 * bl,
        lambda bl: bl * 0,
        lambda bl: bl * -1,
    ],
)
def test_copies_add_and_multiply_keep_the_type(make_copy: Any) -> None:
    """Return a new BoundedList of the same element type and capacity that equals the list result."""
    bl = BoundedList[float]([1.0, 2.0], capacity=8)
    result = make_copy(bl)
    assert type(result) is BoundedList
    assert result is not bl
    assert result == make_copy([1.0, 2.0])
    assert result.capacity == 8
    assert result.element_annotation is float
    result.append(9.0)
    assert bl == [1.0, 2.0]


def test_two_bounded_lists_concatenate() -> None:
    """Concatenate two BoundedLists into a value of the left one's capacity."""
    joined = BoundedList[float]([1.0], capacity=4) + BoundedList[float](
        [2.0], capacity=1
    )
    assert joined == [1.0, 2.0]
    assert joined.capacity == 4


def test_multiply_in_place_repeats_the_elements() -> None:
    """Repeat the elements in place with *=, and clear the value for a count below one."""
    bl = BoundedList[float]([1.0, 2.0], capacity=4)
    same = bl
    bl *= 2
    assert bl is same
    assert bl == [1.0, 2.0, 1.0, 2.0]
    bl *= 0
    assert bl == []


@pytest.mark.parametrize(
    "grow",
    [
        lambda bl: bl * 3,
        lambda bl: 3 * bl,
        lambda bl: bl.__imul__(3),
        lambda bl: bl * 2**62,
        lambda bl: bl + [1.0, 1.0, 1.0],
    ],
)
def test_add_and_multiply_past_the_capacity_raise(grow: Any) -> None:
    """Raise ValueError naming the length the result would have and leave the value as it was."""
    bl = BoundedList[float]([1.0, 2.0], capacity=4)
    with pytest.raises(
        ValueError,
        match=r"BoundedList\[float\] holds at most 4 elements; the value would have \d+",
    ):
        grow(bl)
    assert bl == [1.0, 2.0]


def test_multiply_refuses_what_list_refuses() -> None:
    """Raise TypeError for a count that is not an integer and OverflowError for one past Py_ssize_t."""
    bl = BoundedList[float]([1.0], capacity=4)
    with pytest.raises(TypeError):
        bl * 1.5  # type: ignore[operator]
    with pytest.raises(OverflowError):
        bl * 2**70
    with pytest.raises(TypeError):
        bl + (1.0,)  # type: ignore[operator]


def test_a_deep_copy_of_record_elements_shares_nothing(unique_name: str) -> None:
    """Deep-copy a BoundedList of records with a memo and change the copy without changing the original."""
    with Trace.create(unique_name, [Point(1.0, 2.0)]) as box:
        points = box.points
    memo: dict[int, Any] = {}
    copied = copy.deepcopy([points, points], memo)
    assert copied[0] is copied[1]
    copied[0][0] = Point(5.0, 6.0)
    assert points == [Point(1.0, 2.0)]


def test_a_pickled_bounded_list_reads_back_in_a_spawned_process(
    unique_name: str,
) -> None:
    """Pickle a BoundedList of records read from a box and read it back in a spawned process."""
    with Trace.create(unique_name, [Point(1.0, 2.0), Point(3.0, 4.0)]) as box:
        points = box.points
    assert pickle.loads(pickle.dumps(points)) == points
    assert call_in_child(repr, points) == repr(points)


def test_unpickling_a_count_past_the_capacity_reads_as_corrupt() -> None:
    """Raise the codec's corrupt-value ValueError when pickled bytes hold more elements than the capacity."""
    rebuild, (cls, element, capacity, stored), _ = BoundedList[float](
        [1.0, 2.0], capacity=4
    ).__reduce__()
    forged = (5).to_bytes(4, "little") + stored[4:]
    with pytest.raises(ValueError, match="the stored value is not one Python can hold"):
        rebuild(cls, element, capacity, forged)


def test_repeating_an_empty_value_returns_at_once() -> None:
    """Repeat an empty value any number of times, in place or not, without looping over the count."""
    empty = BoundedList[float](capacity=4)
    assert empty * 2**62 == []
    empty *= 2**62
    assert empty == []


def test_an_element_type_that_cannot_be_hashed_still_builds() -> None:
    """Build and pickle a value whose element annotation carries metadata that cannot be hashed."""
    element = Annotated[float, [1]]
    value = BoundedList[element]([1.0], capacity=4)
    assert value == [1.0]
    assert pickle.loads(pickle.dumps(value)) == [1.0]


@pytest.mark.parametrize("cls", [MyList[float], Points])
def test_a_subclass_reads_back_as_itself_wherever_a_list_goes(
    cls: Any, unique_name: str, names: collections.abc.Callable[[str], str]
) -> None:
    """Read a generic or concrete subclass back as itself from a field, a record member, an optional, a list element and a stream item."""
    origin = get_origin(cls) or cls
    one = Annotated[cls, Capacity(4)]
    record = make_dataclass("Record", [("v", one)], frozen=True)
    holder: Any = type(
        "Holder",
        (SharedBox,),
        {
            "__annotations__": {
                "field": one,
                "member": record,
                "maybe": one | None,
                "rows": Annotated[list[one], Capacity(2)],
            },
            "__module__": __name__,
        },
    )
    assert (
        build_layout(make(one), "sbtest/sub").schema_hash
        == build_layout(
            make(Annotated[list[float], Capacity(4)]), "sbtest/sub"
        ).schema_hash
    )
    with holder.create(unique_name, [1.0], record([2.0]), [3.0], [[4.0]]) as box:
        values = [box.field, box.member.v, box.maybe, *box.rows]
        with SharedStream.create(one, names("s"), capacity=2) as stream:
            reader = stream.reader()
            with stream.sender() as sender:
                sender.send([5.0])
            values.append(next(iter(reader)))
    assert [type(v) for v in values] == [origin] * 5
    assert [list(v) for v in values] == [[1.0], [2.0], [3.0], [4.0], [5.0]]


@pytest.mark.parametrize("cls", [MyList[float], Points])
def test_a_subclass_is_built_copied_and_combined_as_itself(cls: Any) -> None:
    """Build a subclass value and get the same class from copies, slices, + and *."""
    origin = get_origin(cls) or cls
    value = cls([1.0, 2.0], capacity=4)
    results = [
        value,
        copy.copy(value),
        copy.deepcopy(value),
        value.copy(),
        value[:],
        value + [3.0],
        value * 2,
    ]
    assert [type(r) for r in results] == [origin] * 7
    assert repr(value) == f"{origin.__name__}[float]([1.0, 2.0], capacity=4)"


@pytest.mark.parametrize("cls", [BoundedList, MyList])
def test_a_class_without_its_element_type_is_refused(cls: Any) -> None:
    """Refuse to build or declare BoundedList or a generic subclass without an element type."""
    with pytest.raises(TypeError, match=rf"{cls.__name__} needs its element type"):
        cls([1.0], capacity=4)
    with pytest.raises(TypeError, match=rf"T.x: {cls.__name__} needs its element type"):
        build_layout(make(Annotated[cls, Capacity(4)]))


def described(values: list[Any]) -> list[tuple[str, str, object]]:
    return [(type(v).__qualname__, repr(v), v.__getstate__()) for v in values]


def test_a_pickled_subclass_reads_back_as_itself_in_a_spawned_process() -> None:
    """Unpickle subclass values in a spawned process as their own class, with their attributes and slots."""
    generic = MyList[float]([1.0], capacity=4)
    generic.tag = "a"  # type: ignore[attr-defined]
    slotted = Tagged([3.0], capacity=4)
    slotted.tag = "b"
    values = [generic, Points([2.0], capacity=4), slotted]
    assert call_in_child(described, values) == described(values)
    assert [d[2] for d in described(values)] == [
        {"tag": "a"},
        None,
        (None, {"tag": "b"}),
    ]


@pytest.mark.parametrize("cls", [Labelled[float], LabelledPoints])
def test_a_subclass_init_takes_its_own_arguments(cls: Any) -> None:
    """Pass arguments the class's __init__ takes beyond those __new__ uses."""
    value = cls([1.0], capacity=4, tag="y")
    assert value.tag == "y"
    assert value == [1.0]


def test_copies_keep_a_subclass_state() -> None:
    """Copy a subclass's attributes and slots shallowly with copy.copy and deeply with copy.deepcopy."""
    generic = MyList[float]([1.0], capacity=4)
    generic.marks = [1]  # type: ignore[attr-defined]
    slotted = Tagged([2.0], capacity=4)
    slotted.tag = ["b"]
    for value, name in [(generic, "marks"), (slotted, "tag")]:
        shallow = copy.copy(value)
        deep = copy.deepcopy(value)
        assert getattr(shallow, name) is getattr(value, name)
        assert getattr(deep, name) == getattr(value, name)
        assert getattr(deep, name) is not getattr(value, name)
        assert [shallow, deep] == [value, value]
    assert not hasattr(slotted[:], "tag")


def test_a_second_element_type_is_refused() -> None:
    """Refuse two element types, or one for a class whose bases already fix it."""
    with pytest.raises(TypeError, match="BoundedList takes one element type, got 2"):
        BoundedList[float, int]  # type: ignore[misc]
    with pytest.raises(TypeError, match="MyList takes one element type, got 2"):
        MyList[float, int]  # type: ignore[misc]
    with pytest.raises(TypeError, match="Points already has element type float"):
        Points[int]  # type: ignore[misc]


def test_native_collection_is_not_an_annotation() -> None:
    """Refuse NativeCollection itself as a field annotation."""
    with pytest.raises(TypeError, match="T.x"):
        build_layout(make(Annotated[NativeCollection[float], Capacity(4)]))


def test_a_subclass_value_is_written_as_one_byte_copy(unique_name: str) -> None:
    """Write a subclass value to a BoundedList field by copying its bytes, and read the field's own class back."""
    with Scan.create(unique_name, []) as box:
        box.points = Unreadable([1.0, 2.0], capacity=4)
        assert type(box.points) is BoundedList
        assert box.points == [1.0, 2.0]
