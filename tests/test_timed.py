import queue
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated

import numpy as np
import numpy.typing as npt
import pytest
from crossproc import update_in_child

from sharedbox import (
    BoundedDict,
    BoundedSet,
    Capacity,
    MaxShape,
    Shape,
    SharedBox,
    SharedStream,
    Timed,
)

NOW = -(2**63)


class Thermometer(SharedBox):
    temperature: Timed[float] = Timed(0.0, 0)


@dataclass(frozen=True)
class Reading:
    level: Timed[int]
    label: int


class Logbook(SharedBox):
    entries: Annotated[list[Timed[int]], Capacity(4)]
    reading: Reading
    maybe: Timed[int] | None = None


class Scanner(SharedBox):
    image: Timed[Annotated[npt.NDArray[np.uint16], MaxShape((4, 3))]] = Timed(
        np.zeros((2, 3), np.uint16), 0
    )


def collect(values: Iterable[Timed[float]]) -> "queue.Queue[Timed[float]]":
    out: queue.Queue[Timed[float]] = queue.Queue()

    def consume() -> None:
        for value in values:
            out.put(value)

    threading.Thread(target=consume, daemon=True).start()
    return out


def test_a_timed_field_keeps_its_value_and_time_across_processes(
    unique_name: str,
) -> None:
    """Give a watcher and a reader the value and the clock time a spawned process wrote."""
    with Thermometer.create(unique_name) as box:
        seen = collect(box.watch("temperature"))
        before = time.time_ns()
        with Thermometer.attach(unique_name) as attached:
            update_in_child(attached, temperature=Timed.now(21.5))
        watched = seen.get(timeout=20)
        assert watched.value == 21.5
        assert box.temperature.value == 21.5
        assert before <= box.temperature.timestamp_ns <= time.time_ns() + 20_000_000


def test_a_given_timestamp_comes_back_unchanged(unique_name: str) -> None:
    """Read back a value with the timestamp it was written with."""
    with Thermometer.create(unique_name) as box:
        box.temperature = Timed(21.5, 1_700_000_000_123_456_789)
        assert box.temperature == Timed(21.5, 1_700_000_000_123_456_789)
        assert box.temperature.timestamp == pytest.approx(
            1_700_000_000.1234568, abs=1e-6
        )


def test_stamps_follow_the_order_of_writes(unique_name: str) -> None:
    """Stamp a value at its write, not when it was built."""
    with Thermometer.create(unique_name) as box:
        late = Timed.now(1.0)
        box.temperature = Timed.now(2.0)
        first = box.temperature.timestamp_ns
        box.temperature = late
        assert box.temperature.timestamp_ns >= first


def test_marks_inside_collections_and_records_are_stamped(unique_name: str) -> None:
    """Replace the mark of a Timed inside a list, a record and an optional."""
    with Logbook.create(unique_name, [], Reading(Timed(0, 0), 0)) as box:
        box.entries = [Timed.now(1), Timed(2, 9)]
        box.reading = Reading(Timed.now(3), 4)
        box.maybe = Timed.now(5)
        first, second = box.entries
        assert (first.value, second) == (1, Timed(2, 9))
        assert first.timestamp_ns > 0
        assert box.reading.level.value == 3
        assert box.reading.level.timestamp_ns > 0
        assert box.maybe is not None
        assert box.maybe.timestamp_ns > 0
        assert NOW not in {first.timestamp_ns, box.reading.level.timestamp_ns}


def test_a_stream_item_holds_a_timed_value(unique_name: str) -> None:
    """Stamp a Timed item at its send and keep a given timestamp."""
    with SharedStream.create(Timed[int], unique_name, capacity=4) as stream:
        reader = stream.reader()
        with stream.sender() as sender:
            sender.send(Timed.now(5))
            sender.send(Timed(6, 42))
        stamped = reader.receive()
        given = reader.receive()
        assert (stamped.value, given) == (5, Timed(6, 42))
        assert stamped.timestamp_ns > 0


def test_timed_holds_a_max_shape_array(unique_name: str) -> None:
    """Read back the shape written into a MaxShape array inside a Timed, with a stamp."""
    with Scanner.create(unique_name) as box:
        box.image = Timed.now(np.arange(6, dtype=np.uint16).reshape(2, 3))
        assert box.image.value.shape == (2, 3)
        assert box.image.value.tolist() == [[0, 1, 2], [3, 4, 5]]
        assert box.image.timestamp_ns > 0


def test_a_timed_read_is_reused_until_written(unique_name: str) -> None:
    """Return the same object from reads until the field is written."""
    with Thermometer.create(unique_name) as box:
        first = box.temperature
        assert box.temperature is first
        box.temperature = Timed(1.0, 5)
        assert box.temperature is not first


@pytest.mark.parametrize("value", [21.5, (21.5, 0), Timed("x", 0)])
def test_a_write_takes_only_a_timed(unique_name: str, value: object) -> None:
    """Refuse a value that is not a Timed of the declared type and keep the stored one."""
    with Thermometer.create(unique_name) as box:
        box.temperature = Timed(3.0, 7)
        with pytest.raises(TypeError):
            box.temperature = value  # type: ignore[assignment]
        assert box.temperature == Timed(3.0, 7)


def test_a_timestamp_past_64_bits_is_refused(unique_name: str) -> None:
    """Raise the error an int field raises for 2**63 and keep the stored value."""
    with Thermometer.create(unique_name) as box:
        box.temperature = Timed(3.0, 7)
        with pytest.raises(OverflowError, match="signed 64-bit integer"):
            box.temperature = Timed(1.0, 2**63)
        assert box.temperature == Timed(3.0, 7)


def test_timed_is_refused_where_it_cannot_go() -> None:
    """Raise TypeError at class definition for a bare Timed, a Timed element of a set or key of a dict and a misplaced MaxShape."""
    with pytest.raises(TypeError, match="Timed needs its value type"):

        class Bare(SharedBox):
            value: Timed  # type: ignore[type-arg]

    with pytest.raises(TypeError, match="a Timed"):

        class InPlainSet(SharedBox):
            values: Annotated[set[Timed[int]], Capacity(4)]

    with pytest.raises(TypeError, match="a Timed"):

        class InFrozenSet(SharedBox):
            values: Annotated[frozenset[Timed[int]], Capacity(4)]

    with pytest.raises(TypeError, match="a Timed"):

        class AsPlainKey(SharedBox):
            values: Annotated[dict[Timed[int], int], Capacity(4)]

    with pytest.raises(TypeError, match="a Timed"):

        class InSet(SharedBox):
            values: Annotated[BoundedSet[Timed[int]], Capacity(4)]

    with pytest.raises(TypeError, match="a Timed"):

        class AsKey(SharedBox):
            values: Annotated[BoundedDict[Timed[int], int], Capacity(4)]

    with pytest.raises(TypeError, match="MaxShape applies only to"):

        class Maybe(SharedBox):
            image: Timed[Annotated[npt.NDArray[np.uint16], MaxShape((4, 3))]] | None = (
                None
            )


def test_receive_into_does_not_reach_an_array_inside_a_timed(
    unique_name: str,
) -> None:
    """Raise TypeError from receive_into on a stream whose only array sits inside a Timed."""
    item = Timed[Annotated[npt.NDArray[np.uint16], Shape((2,))]]
    with SharedStream.create(item, unique_name, capacity=4) as stream:
        reader = stream.reader()
        stream.sender().send(Timed.now(np.zeros(2, np.uint16)))
        with pytest.raises(TypeError, match="holds no array"):
            reader.receive_into(None)
