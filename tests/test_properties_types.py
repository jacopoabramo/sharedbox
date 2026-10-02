# ruff: noqa: DTZ001, DTZ901, RUF012
import datetime
import decimal
import enum
import uuid
from collections.abc import Iterator
from typing import Annotated, Any

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from sharedbox import Capacity, SharedBox


class Level(enum.IntEnum):
    LOW = 1
    MID = 2
    HIGH = 3


class Kinds(SharedBox):
    when: datetime.datetime = datetime.datetime(2026, 1, 1)
    day: datetime.date = datetime.date(2026, 1, 1)
    at: datetime.time = datetime.time()
    span: datetime.timedelta = datetime.timedelta()
    uid: uuid.UUID = uuid.UUID(int=0)
    z: complex = 0j
    price: Annotated[decimal.Decimal, Capacity(32)] = decimal.Decimal(0)
    level: Level = Level.LOW
    pick: int | bool | float | Annotated[str, Capacity(8)] | None = None
    numbers: Annotated[list[int], Capacity(8)] = []
    table: Annotated[dict[Annotated[str, Capacity(4)], float], Capacity(4)] = {}


@pytest.fixture(scope="module")
def box() -> Iterator[Kinds]:
    name = f"sbtest-{uuid.uuid4().hex[:16]}"
    with Kinds.create(name) as created:
        yield created
    Kinds.unlink(name)


OFFSETS = st.integers(-1439, 1439).map(
    lambda m: datetime.timezone(datetime.timedelta(minutes=m))
)
DATETIMES = st.one_of(
    st.datetimes(),
    st.datetimes(timezones=OFFSETS),
)


def assert_round_trip(box: Kinds, field: str, value: Any) -> None:
    setattr(box, field, value)
    got = getattr(box, field)
    assert got == value
    assert type(got) is type(value)


@given(DATETIMES)
@example(datetime.datetime.min)
@example(datetime.datetime.max)
@example(
    datetime.datetime.min.replace(
        tzinfo=datetime.timezone(-datetime.timedelta(minutes=1439))
    )
)
@example(
    datetime.datetime.max.replace(
        tzinfo=datetime.timezone(datetime.timedelta(minutes=1439))
    )
)
@example(datetime.datetime(2026, 11, 1, 1, 30, fold=1))
def test_datetimes_round_trip(box: Kinds, value: datetime.datetime) -> None:
    """Check that naive and fixed-offset datetimes, the range ends and fold included, read back with the same offset and fold."""
    assert_round_trip(box, "when", value)
    assert box.when.utcoffset() == value.utcoffset()
    assert box.when.fold == value.fold


@given(st.dates(), st.times(timezones=st.one_of(st.none(), OFFSETS)), st.timedeltas())
@example(datetime.date.min, datetime.time.max, datetime.timedelta.min)
@example(datetime.date.max, datetime.time.min, datetime.timedelta.max)
def test_dates_times_and_timedeltas_round_trip(
    box: Kinds, day: datetime.date, at: datetime.time, span: datetime.timedelta
) -> None:
    """Check that dates, times with or without an offset, and timedeltas over their whole ranges read back equal."""
    assert_round_trip(box, "day", day)
    assert_round_trip(box, "at", at)
    assert box.at.utcoffset() == at.utcoffset()
    assert_round_trip(box, "span", span)


@given(
    st.uuids(),
    st.complex_numbers(allow_nan=False),
    st.decimals(places=None).filter(lambda d: len(str(d)) <= 32),
)
def test_uuids_complexes_and_decimals_round_trip(
    box: Kinds, uid: uuid.UUID, z: complex, price: decimal.Decimal
) -> None:
    """Check that UUIDs, complex numbers and decimals that fit the capacity read back equal."""
    assert_round_trip(box, "uid", uid)
    assert_round_trip(box, "z", z)
    box.price = price
    assert str(box.price) == str(price)


@given(
    st.sampled_from(Level),
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(-(2**63), 2**63 - 1),
        st.floats(allow_nan=False),
        st.text(max_size=2),
    ),
)
def test_enums_and_union_members_round_trip(
    box: Kinds, level: Level, pick: Any
) -> None:
    """Check that every enum member and every union member type reads back as the same type and value."""
    assert_round_trip(box, "level", level)
    assert_round_trip(box, "pick", pick)


@given(
    st.lists(st.integers(-(2**63), 2**63 - 1), max_size=8),
    st.dictionaries(st.text(max_size=1), st.floats(allow_nan=False), max_size=4),
)
def test_collections_round_trip_from_empty_to_full(
    box: Kinds, numbers: list[int], table: dict[str, float]
) -> None:
    """Check that lists and dicts of every length up to their capacity read back equal."""
    assert_round_trip(box, "numbers", numbers)
    assert_round_trip(box, "table", table)
