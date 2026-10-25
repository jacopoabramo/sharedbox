import collections.abc
import enum
import queue
import threading
from dataclasses import dataclass
from typing import Annotated

import pytest
from crossproc import update_in_child

from sharedbox import BoundedList, Capacity, SharedBox, SharedStream, field
from sharedbox._layout import build_layout


class Scan(SharedBox):
    points: Annotated[BoundedList[float], Capacity(8)]


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
            other.update(v=one.v)
        assert str(bounded.value) == str(listed.value)
        assert list(other.v) == [second]


@pytest.mark.filterwarnings("ignore:nanobind")
def test_a_bounded_list_that_was_never_built_takes_the_per_element_path(
    unique_name: str,
) -> None:
    """Write a BoundedList made by __new__ alone as an empty sequence."""
    with Scan.create(unique_name, [1.0]) as box:
        box.update(points=BoundedList.__new__(BoundedList))
        assert box.points == []
