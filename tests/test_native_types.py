"""Types and Segment with kinds 6 to 12, through the native module alone."""

import datetime
import decimal
import struct
import uuid

import pytest

from sharedbox._layout import NativeField
from sharedbox._native import LAYOUT_VERSION, Segment, Types

COMPLEX, DATE, TIME, DATETIME, TIMEDELTA, UUID, DECIMAL = range(6, 13)
FIELDS = [
    NativeField(0, 16, DATETIME),
    NativeField(16, 16, COMPLEX),
    NativeField(32, 16, TIME),
    NativeField(48, 16, UUID),
    NativeField(64, 12, TIMEDELTA),
    NativeField(76, 4, DATE),
    NativeField(80, 20, DECIMAL),
]
NAMES = ["T.when", "T.z", "T.at", "T.id", "T.span", "T.day", "T.price"]
RECORD_SIZE = 104
SCHEMA = 0x7E5
PLUS_TWO = datetime.timezone(datetime.timedelta(hours=2))


class Shifting(datetime.tzinfo):
    """A zone whose offset depends on the month, as a named zone's does."""

    def utcoffset(self, dt: datetime.datetime | None) -> datetime.timedelta | None:
        if dt is None:
            return None
        return datetime.timedelta(hours=1 if dt.month < 6 else 2)

    def dst(self, dt: datetime.datetime | None) -> datetime.timedelta | None:
        return None

    def tzname(self, dt: datetime.datetime | None) -> str:
        return "Shifting"


class HalfMinute(datetime.tzinfo):
    """A zone 30 seconds east of UTC, which no whole number of minutes gives."""

    def utcoffset(self, dt: datetime.datetime | None) -> datetime.timedelta:
        return datetime.timedelta(seconds=30)

    def dst(self, dt: datetime.datetime | None) -> datetime.timedelta | None:
        return None

    def tzname(self, dt: datetime.datetime | None) -> str:
        return "HalfMinute"


def types() -> Types:
    return Types(FIELDS, NAMES, b"", {}, [])


def create(name: str) -> Segment:
    return Segment.create(
        name, FIELDS, NAMES, RECORD_SIZE, SCHEMA, 1.0, [], types=types()
    )


@pytest.mark.parametrize(
    ("index", "value"),
    [
        (0, datetime.datetime(2026, 10, 2, 12, 30, 15, 123456)),  # noqa: DTZ001
        (0, datetime.datetime(2026, 10, 2, 12, 30, tzinfo=PLUS_TWO)),
        (0, datetime.datetime(2026, 11, 1, 1, 30, fold=1)),  # noqa: DTZ001
        (0, datetime.datetime.min),  # noqa: DTZ901
        (0, datetime.datetime.max),  # noqa: DTZ901
        (0, datetime.datetime.max.replace(tzinfo=PLUS_TWO)),
        (
            0,
            datetime.datetime.min.replace(
                tzinfo=datetime.timezone(-datetime.timedelta(hours=23, minutes=59))
            ),
        ),
        (1, complex(1.5, -2.25)),
        (1, 3.0),
        (2, datetime.time(23, 59, 59, 999999)),
        (2, datetime.time(8, 0, tzinfo=PLUS_TWO)),
        (3, uuid.UUID("12345678-1234-5678-1234-567812345678")),
        (4, datetime.timedelta.min),
        (4, datetime.timedelta.max),
        (5, datetime.date(1, 1, 1)),
        (5, datetime.date(9999, 12, 31)),
        (6, decimal.Decimal("-1.50")),
        (6, decimal.Decimal("sNaN")),
    ],
)
def test_values_round_trip(unique_name: str, index: int, value: object) -> None:
    """Check that each kind from 6 to 12 reads back equal, with the same type and offset."""
    segment = create(unique_name)
    t = types()
    segment.set([(index, value)], t)
    got = segment.get(index, t)
    if isinstance(value, decimal.Decimal):
        assert str(got) == str(value)
    else:
        assert got == value
        assert type(got) is (complex if index == 1 else type(value))
    if isinstance(value, (datetime.datetime, datetime.time)):
        assert isinstance(got, (datetime.datetime, datetime.time))
        assert got.utcoffset() == value.utcoffset()
        assert got.fold == value.fold
    segment.close()


def test_an_aware_value_comes_back_with_a_fixed_offset(unique_name: str) -> None:
    """Check that a zone is kept as its offset at that instant, and that one offset reads back as one object."""
    segment = create(unique_name)
    t = types()
    segment.set([(0, datetime.datetime(2026, 7, 1, 9, tzinfo=Shifting()))], t)
    first = segment.get(0, t)
    assert isinstance(first, datetime.datetime)
    assert first.tzinfo == datetime.timezone(datetime.timedelta(hours=2))
    again = segment.get(0, t)
    assert isinstance(again, datetime.datetime)
    assert again.tzinfo is first.tzinfo
    segment.close()


@pytest.mark.parametrize(
    ("index", "value", "error"),
    [
        (5, datetime.datetime(2026, 1, 1), TypeError),  # noqa: DTZ001
        (0, datetime.date(2026, 1, 1), TypeError),
        (0, datetime.datetime(2026, 1, 1, tzinfo=HalfMinute()), ValueError),
        (2, datetime.time(8, tzinfo=Shifting()), ValueError),
        (6, decimal.Decimal("1" * 21), ValueError),
        (6, 1.5, TypeError),
        (1, True, TypeError),
        (3, "12345678-1234-5678-1234-567812345678", TypeError),
    ],
)
def test_bad_values_are_refused(
    unique_name: str, index: int, value: object, error: type[Exception]
) -> None:
    """Check that a value of the wrong type or one the layout cannot keep raises, naming the field."""
    segment = create(unique_name)
    with pytest.raises(error, match=NAMES[index]):
        segment.set([(index, value)], types())
    segment.close()


def test_a_stored_value_that_fails_the_checks_raises(unique_name: str) -> None:
    """Check that bytes no Python value has, written raw, raise ValueError on read."""
    segment = create(unique_name)
    segment._write([(5, struct.pack("<i", 0))])
    with pytest.raises(ValueError, match="T.day"):
        segment.get(5, types())
    segment.close()


def test_a_field_above_kind_5_needs_types(unique_name: str) -> None:
    """Check that reading a kind above 5 without the class's Types raises TypeError."""
    segment = create(unique_name)
    with pytest.raises(TypeError, match="Types"):
        segment.get(5)
    segment.close()


def test_the_table_is_compared_at_attach(unique_name: str) -> None:
    """Check that attach with the same Types opens the box, and that read_versioned returns the raw bytes."""
    owner = create(unique_name)
    other = Segment.attach(unique_name, NAMES, SCHEMA, 1.0, types())
    owner.set([(5, datetime.date(2026, 1, 2))], types())
    version, raw = other.read_versioned(5)
    assert (version, raw) == (
        1,
        struct.pack("<i", datetime.date(2026, 1, 2).toordinal()),
    )
    assert other.layout_version == LAYOUT_VERSION
    other.close()
    owner.close()


def test_a_layout_1_box_opens_with_types(unique_name: str) -> None:
    """Check that a box with the 1.0 layout attaches, reads and writes, and reports 1.0."""
    fields = [NativeField(0, 8, 1), NativeField(8, 8, 2)]
    owner = Segment._create_layout_1(
        unique_name, fields, ["P.x", "P.y"], 16, 0x11, 1.0, [(0, 5)]
    )
    other = Segment.attach(
        unique_name,
        ["P.x", "P.y"],
        0x11,
        1.0,
        Types(fields, ["P.x", "P.y"], b"", {}, []),
    )
    assert other.layout_version == (1, 0)
    other.set([(1, 2.5)])
    assert other.get_dict(("x", "y")) == {"x": 5, "y": 2.5}
    other.close()
    owner.close()


class FarAhead(datetime.datetime):
    """A datetime whose own utcoffset gives 45 days, whose minutes overflow 16 bits."""

    def utcoffset(self) -> datetime.timedelta:
        return datetime.timedelta(days=45)


class NumberOffset(datetime.datetime):
    """A datetime whose own utcoffset gives an int rather than a timedelta."""

    def utcoffset(self) -> int:  # type: ignore[override]
        return 5


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (FarAhead(2026, 1, 1), ValueError),
        (NumberOffset(2026, 1, 1), TypeError),
    ],
)
def test_an_offset_no_timezone_can_give_is_refused(
    unique_name: str, value: datetime.datetime, error: type[Exception]
) -> None:
    """Check that a utcoffset of a day or more, or not a timedelta, raises naming the field."""
    segment = create(unique_name)
    with pytest.raises(error, match="T.when"):
        segment.set([(0, value)], types())
    segment.close()


def test_a_stored_decimal_that_is_not_utf8_raises(unique_name: str) -> None:
    """Check that a decimal field holding bytes that are not UTF-8 raises ValueError naming the field."""
    segment = create(unique_name)
    segment._write([(6, b"\xff")])
    with pytest.raises(ValueError, match="T.price"):
        segment.get(6, types())
    segment.close()


@pytest.mark.parametrize(("index", "data"), [(0, b""), (1, b"1" * 21)])
def test_types_decode_refuses_bytes_of_the_wrong_size(index: int, data: bytes) -> None:
    """Check that Types.decode raises ValueError naming the field for bytes the field cannot hold."""
    labels = ["P.n", "P.price"]
    t = Types([NativeField(0, 8, 1), NativeField(8, 20, DECIMAL)], labels, b"", {}, [])
    with pytest.raises(ValueError, match=labels[index]):
        t.decode(index, data)
