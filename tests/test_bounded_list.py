import collections.abc
import queue
from dataclasses import dataclass
from typing import Annotated

import pytest

from sharedbox import BoundedList, Capacity, SharedBox, SharedStream, field
from sharedbox._layout import build_layout


class Scan(SharedBox):
    points: Annotated[BoundedList[float], Capacity(8)]


@dataclass(frozen=True)
class Shot:
    values: Annotated[BoundedList[int], Capacity(4)]


class Places(SharedBox):
    shot: Shot
    maybe: Annotated[BoundedList[int], Capacity(4)] | None
    rows: Annotated[list[Annotated[BoundedList[int], Capacity(2)]], Capacity(2)]
    points: BoundedList[int] = field(capacity=4)


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


def test_setting_an_element_checks_it_as_a_write_would(unique_name: str) -> None:
    """Refuse an element of the wrong type with the field's message, and change only the value read."""
    with Scan.create(unique_name, [1.0, 2.5]) as box:
        points = box.points
        with pytest.raises(TypeError, match="Scan.points expects float, got str"):
            points[0] = "x"  # type: ignore[assignment]
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
