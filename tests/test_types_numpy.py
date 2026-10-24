import dataclasses
from decimal import Decimal
from fractions import Fraction
from typing import Annotated

import numpy as np
import pytest

from sharedbox import Capacity, SharedBox, SharedStream, field


class Counter(SharedBox):
    parcels: int = 0
    weight: float = 0.0
    ready: bool = False
    phase: complex = 0j


@dataclasses.dataclass(frozen=True)
class Parcel:
    count: int
    weight: float


class Depot(SharedBox):
    parcel: Parcel = Parcel(0, 0.0)
    counts: Annotated[list[int], Capacity(4)] = field(default_factory=list)


def test_numpy_numbers_are_taken_by_python_number_fields(unique_name: str) -> None:
    """Take numpy scalars in int, float, bool and complex fields and read back Python numbers."""
    with Counter.create(unique_name) as counter:
        counter.parcels = np.array([3, 7, 5], np.uint16).max()
        counter.weight = np.float32(1.5)  # type: ignore[assignment]
        counter.ready = np.True_  # type: ignore[assignment]
        counter.phase = np.complex64(1 + 2j)  # type: ignore[assignment]
        assert (type(counter.parcels), counter.parcels) == (int, 7)
        assert (type(counter.weight), counter.weight) == (float, 1.5)
        assert counter.ready is True
        assert (type(counter.phase), counter.phase) == (complex, 1 + 2j)


def test_numpy_numbers_are_taken_inside_records_and_collections(
    unique_name: str,
) -> None:
    """Take numpy scalars as record members and list elements and read back Python numbers."""
    with Depot.create(unique_name) as depot:
        depot.parcel = Parcel(np.int16(2), np.float32(0.5))  # type: ignore[arg-type]
        depot.counts = [np.int8(1), np.uint32(2)]  # type: ignore[list-item]
        assert depot.parcel == Parcel(2, 0.5)
        assert type(depot.parcel.count) is int
        assert [type(c) for c in depot.counts] == [int, int]


def test_a_stream_of_ints_takes_numpy_integers(unique_name: str) -> None:
    """Send numpy integers through an int stream and receive Python ints."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        with stream.sender() as sender:
            sender.send(np.int32(4))  # type: ignore[arg-type]
        assert [(type(i), i) for i in reader] == [(int, 4)]


@pytest.mark.parametrize("value", [np.uint64(2**63), np.uint64(2**64 - 1)])
def test_a_numpy_integer_past_int64_is_refused(
    unique_name: str, value: np.uint64
) -> None:
    """Raise OverflowError for numpy integers an int field cannot hold."""
    with Counter.create(unique_name) as counter, pytest.raises(OverflowError):
        counter.parcels = value  # type: ignore[assignment]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parcels", True),
        ("parcels", np.True_),
        ("parcels", "5"),
        ("parcels", 5.0),
        ("weight", np.True_),
        ("weight", Decimal("1.5")),
        ("weight", np.array(1.5)),
        ("ready", 1),
    ],
)
def test_values_outside_the_input_rule_are_refused(
    unique_name: str, field: str, value: object
) -> None:
    """Raise TypeError for bools in number fields, text, Decimal, 0-d float arrays and ints in bool fields."""
    with Counter.create(unique_name) as counter, pytest.raises(TypeError):
        setattr(counter, field, value)


def test_values_the_input_rule_adds_are_taken(unique_name: str) -> None:
    """Take a 0-d integer array in an int field and a Fraction in a float field."""
    with Counter.create(unique_name) as counter:
        counter.parcels = np.array(5)  # type: ignore[assignment]
        counter.weight = Fraction(1, 4)  # type: ignore[assignment]
        assert (counter.parcels, counter.weight) == (5, 0.25)


class Wide(SharedBox):
    raw: np.int64 = np.int64(0)
    ratio: np.float64 = np.float64(0.0)
    phase: np.complex128 = np.complex128(0)
    ready: np.bool_ = np.False_


@dataclasses.dataclass(frozen=True)
class Reading:
    raw: np.int64
    ratio: np.float64


class Log(SharedBox):
    reading: Reading = Reading(np.int64(0), np.float64(0.0))
    raws: Annotated[list[np.int64], Capacity(4)] = field(default_factory=list)


def test_numpy_types_with_python_bytes_read_back_as_numpy(unique_name: str) -> None:
    """Read np.int64, np.float64, np.complex128 and np.bool_ fields back as those types, whatever was written."""
    with Wide.create(unique_name) as box:
        box.update(raw=5, ratio=0.5, phase=1j, ready=True)
        assert [type(v) for v in (box.raw, box.ratio, box.phase, box.ready)] == [
            np.int64,
            np.float64,
            np.complex128,
            np.bool_,
        ]
        assert (box.raw, box.ratio, box.phase, box.ready) == (5, 0.5, 1j, True)  # type: ignore[comparison-overlap]
        assert type(box.snapshot()["raw"]) is np.int64


def test_numpy_members_and_elements_read_back_as_numpy(unique_name: str) -> None:
    """Read np.int64 record members and list elements back as np.int64."""
    with Log.create(unique_name) as log:
        log.reading = Reading(3, 0.25)  # type: ignore[arg-type]
        log.raws = [1, np.int32(2)]  # type: ignore[list-item]
        assert [type(v) for v in (log.reading.raw, log.reading.ratio)] == [
            np.int64,
            np.float64,
        ]
        assert [type(v) for v in log.raws] == [np.int64, np.int64]


def test_a_stream_of_np_int64_items_receives_np_int64(unique_name: str) -> None:
    """Receive np.int64 items from a stream declared np.int64, sent as Python ints."""
    with SharedStream.create(np.int64, unique_name, capacity=4) as stream:
        reader = stream.reader()
        with stream.sender() as sender:
            sender.send(7)  # type: ignore[arg-type]
        assert [(type(i), i) for i in reader] == [(np.int64, 7)]  # type: ignore[comparison-overlap]


class Plain(SharedBox, identity="counter/1"):
    raw: int = 0


class Numpy(SharedBox, identity="counter/1"):
    raw: np.int64 = np.int64(0)


def test_int_and_np_int64_classes_of_one_identity_share_a_box(unique_name: str) -> None:
    """Attach an np.int64 class to a box an int class created, each reading its own declared type."""
    with Plain.create(unique_name, 9) as plain, Numpy.attach(unique_name) as numpy_view:
        assert (type(plain.raw), type(numpy_view.raw)) == (int, np.int64)
        assert numpy_view.raw == 9
