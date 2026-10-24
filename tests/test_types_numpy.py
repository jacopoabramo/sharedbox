import dataclasses
import datetime
from decimal import Decimal
from fractions import Fraction
from typing import Annotated

import numpy as np
import pytest

from sharedbox import Capacity, SharedBox, SharedStream, field
from sharedbox._layout import build_layout


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


class Scale(SharedBox):
    grams: np.uint16 = np.uint16(0)
    reading: np.float32 = np.float32(0.0)
    small: np.float16 = np.float16(0.0)
    count: np.uint64 = np.uint64(0)
    level: np.int8 = np.int8(0)
    phase: np.complex64 = np.complex64(0)


@dataclasses.dataclass(frozen=True)
class Weighing:
    grams: np.uint16
    parts: Annotated[list[np.int8], Capacity(4)]


def test_fixed_width_fields_round_trip_with_their_type(unique_name: str) -> None:
    """Store Python and numpy numbers in fixed-width fields and read back the declared numpy type."""
    with Scale.create(unique_name) as scale:
        scale.grams = 1200  # type: ignore[assignment]
        scale.reading = 0.1  # type: ignore[assignment]
        scale.small = 0.1  # type: ignore[assignment]
        scale.level = np.int64(-3)  # type: ignore[assignment]
        scale.phase = 1.5 - 2j  # type: ignore[assignment]
        assert (type(scale.grams), scale.grams) == (np.uint16, np.uint16(1200))
        assert (type(scale.reading), scale.reading) == (np.float32, np.float32(0.1))
        assert (type(scale.small), scale.small) == (np.float16, np.float16(0.1))
        assert (type(scale.level), scale.level) == (np.int8, np.int8(-3))
        assert (type(scale.phase), scale.phase) == (
            np.complex64,
            np.complex64(1.5 - 2j),
        )
        scale.reading = float("nan")  # type: ignore[assignment]
        assert np.isnan(scale.reading)
        assert type(scale.reading) is np.float32


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("grams", 70000),
        ("grams", -1),
        ("level", 128),
        ("reading", 1e300),
        ("small", 70000.0),
        ("small", 65520.0),
        ("reading", float.fromhex("0x1.ffffffp127")),
        ("phase", complex(1e300, 0)),
        ("count", 2**64),
        ("count", -1),
        ("count", np.int64(-1)),
    ],
)
def test_values_past_the_width_raise_overflow(
    unique_name: str, field: str, value: object
) -> None:
    """Raise OverflowError for integers out of range and finite floats past the width."""
    with Scale.create(unique_name) as scale, pytest.raises(OverflowError):
        setattr(scale, field, value)


@pytest.mark.parametrize(
    ("field", "value", "stored"),
    [
        ("small", 65519.99, np.float16(65504)),
        ("reading", float.fromhex("0x1.fffffep127"), np.finfo(np.float32).max),
        ("count", 2**64 - 1, np.uint64(2**64 - 1)),
        ("count", np.uint64(2**64 - 1), np.uint64(2**64 - 1)),
        ("small", float("inf"), np.float16("inf")),
    ],
)
def test_values_at_the_edge_of_the_width_are_stored(
    unique_name: str, field: str, value: object, stored: np.generic
) -> None:
    """Store values that round to the largest finite value, the largest uint64, and infinities."""
    with Scale.create(unique_name) as scale:
        setattr(scale, field, value)
        assert getattr(scale, field) == stored


def test_fixed_width_members_and_stream_items_round_trip(unique_name: str) -> None:
    """Send a record of fixed-width members through a stream and receive the declared types."""
    with SharedStream.create(Weighing, unique_name, capacity=4) as stream:
        reader = stream.reader()
        with stream.sender() as sender:
            sender.send(Weighing(np.uint16(5), [1, -2]))  # type: ignore[list-item]
        (got,) = list(reader)
        assert (type(got.grams), got.grams) == (np.uint16, np.uint16(5))
        assert [(type(p), p) for p in got.parts] == [
            (np.int8, np.int8(1)),
            (np.int8, np.int8(-2)),
        ]


@pytest.mark.parametrize(
    ("hint", "same_as"),
    [
        (np.intc, np.int32),
        (np.longlong, np.int64),
        (np.double, np.float64),
        (np.int_, np.int64),
    ],
)
def test_numpy_aliases_declare_the_kind_of_their_width(
    hint: type, same_as: type
) -> None:
    """Give an alias of a numpy type the schema hash of the type it names."""
    one = type("A", (), {"__annotations__": {"x": hint}})
    two = type("B", (), {"__annotations__": {"x": same_as}})
    assert (
        build_layout(one, identity="alias/1").schema_hash
        == build_layout(two, identity="alias/1").schema_hash
    )


@pytest.mark.skipif(
    np.dtype(np.longdouble).itemsize == 8, reason="longdouble is float64 here"
)
def test_a_numpy_type_with_no_kind_is_refused() -> None:
    """Refuse np.longdouble where it is wider than 64 bits."""
    with pytest.raises(TypeError, match="unsupported annotation"):
        build_layout(type("C", (), {"__annotations__": {"x": np.longdouble}}))


class Delivery(SharedBox):
    left_at: np.datetime64 = np.datetime64("NaT")


def test_a_datetime64_field_stores_nanoseconds(unique_name: str) -> None:
    """Store a datetime64 of any unit and read it back in nanoseconds."""
    with Delivery.create(unique_name) as delivery:
        assert np.isnat(delivery.left_at)
        delivery.left_at = np.datetime64("2026-10-24T09:30", "m")
        assert delivery.left_at == np.datetime64("2026-10-24T09:30:00.000000000")
        assert np.datetime_data(delivery.left_at.dtype) == ("ns", 1)
        delivery.left_at = np.datetime64("2026-10", "M")
        assert delivery.left_at == np.datetime64("2026-10-01", "ns")
        delivery.left_at = np.datetime64(-(2**63) + 1, "ns")
        assert delivery.left_at.astype(np.int64) == -(2**63) + 1


@pytest.mark.parametrize(
    "value",
    [
        np.datetime64("3000-01-01"),
        np.datetime64("1600-01-01", "s"),
        np.datetime64(10**15, "Y"),
        np.datetime64(10**17, "M"),
    ],
)
def test_a_datetime64_past_2262_or_before_1678_raises_overflow(
    unique_name: str, value: np.datetime64
) -> None:
    """Raise OverflowError for a datetime64 that nanoseconds since 1970 cannot hold."""
    with Delivery.create(unique_name) as delivery, pytest.raises(OverflowError):
        delivery.left_at = value


@pytest.mark.parametrize("value", [0, datetime.datetime(2026, 10, 24), "2026-10-24"])  # noqa: DTZ001
def test_a_datetime64_field_refuses_other_values(
    unique_name: str, value: object
) -> None:
    """Raise TypeError for an int, a datetime and a string in a datetime64 field."""
    with Delivery.create(unique_name) as delivery, pytest.raises(TypeError):
        delivery.left_at = value  # type: ignore[assignment]


class Envelope(SharedBox):
    stamp: float | int = 0
    weight: float | np.int16 = 0.0


def test_a_union_keeps_a_value_in_its_own_family(unique_name: str) -> None:
    """Store numpy integers through the integer member and numpy floats through the float member."""
    with Envelope.create(unique_name) as envelope:
        envelope.stamp = np.int64(5)  # type: ignore[assignment]
        assert (type(envelope.stamp), envelope.stamp) == (int, 5)
        envelope.stamp = np.float32(0.5)  # type: ignore[assignment]
        assert (type(envelope.stamp), envelope.stamp) == (float, 0.5)
        envelope.weight = 3
        assert (type(envelope.weight), envelope.weight) == (np.int16, np.int16(3))  # type: ignore[comparison-overlap]


class Sample(SharedBox):
    level: complex | np.float32 = 0j


def test_a_numpy_float64_goes_to_a_real_member(unique_name: str) -> None:
    """Store a numpy float64 through the first real member, as a numpy float32 is stored."""
    with Sample.create(unique_name) as sample:
        sample.level = np.float64(2.0)
        assert type(sample.level) is np.float32


def test_an_overflowing_member_does_not_pass_to_the_next(unique_name: str) -> None:
    """Raise OverflowError when the integer member cannot hold the value, without trying the float one."""
    with Envelope.create(unique_name) as envelope, pytest.raises(OverflowError):
        envelope.weight = 70000
