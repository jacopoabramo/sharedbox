from __future__ import annotations

import struct
import sys
import uuid
from collections.abc import Iterator
from typing import Annotated

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from sharedbox import Capacity, SharedBox

INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1


class Codec(SharedBox):
    count: int = 0
    ratio: float = 0.0
    flag: bool = False
    s1: Annotated[str, Capacity(1)] = ""
    s4: Annotated[str, Capacity(4)] = ""
    s64: Annotated[str, Capacity(64)] = ""
    s4096: Annotated[str, Capacity(4096)] = ""
    b1: Annotated[bytes, Capacity(1)] = b""
    b16: Annotated[bytes, Capacity(16)] = b""
    b4096: Annotated[bytes, Capacity(4096)] = b""


CAPACITY = {
    "s1": 1,
    "s4": 4,
    "s64": 64,
    "s4096": 4096,
    "b1": 1,
    "b16": 16,
    "b4096": 4096,
}
# Surrogates are excluded by default; a lone one is a str that UTF-8 cannot encode.
ANY_TEXT = st.text(st.characters(exclude_categories=()), max_size=1200)


@pytest.fixture(scope="module")
def box() -> Iterator[Codec]:
    name = f"sbtest-{uuid.uuid4().hex[:16]}"
    with Codec.create(name) as created:
        yield created
    Codec.unlink(name)


@given(field=st.sampled_from(["s1", "s4", "s64", "s4096"]), value=ANY_TEXT)
@example(field="s4", value="\ud800")
@example(field="s4", value="\U0001f600")
@example(field="s1", value="é")
def test_str_round_trips_or_is_refused(box: Codec, field: str, value: str) -> None:
    before = getattr(box, field)
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        with pytest.raises(UnicodeEncodeError):
            setattr(box, field, value)
        assert getattr(box, field) == before
        return
    if size > CAPACITY[field]:
        with pytest.raises(
            ValueError, match=f"Codec.{field} holds at most {CAPACITY[field]} bytes"
        ):
            setattr(box, field, value)
        assert getattr(box, field) == before
        return
    setattr(box, field, value)
    assert getattr(box, field) == value


BYTES_LIKE = st.one_of(
    st.binary(max_size=5000),
    st.binary(max_size=5000).map(bytearray),
    st.binary(max_size=5000).map(lambda data: memoryview(data)),
    st.binary(max_size=10000).map(lambda data: memoryview(data)[::2]),
)


@given(field=st.sampled_from(["b1", "b16", "b4096"]), value=BYTES_LIKE)
def test_bytes_round_trip_or_are_refused(
    box: Codec, field: str, value: bytes | bytearray | memoryview
) -> None:
    before = getattr(box, field)
    expected = bytes(value)
    if len(expected) > CAPACITY[field]:
        with pytest.raises(
            ValueError, match=f"Codec.{field} holds at most {CAPACITY[field]} bytes"
        ):
            setattr(box, field, value)
        assert getattr(box, field) == before
        return
    setattr(box, field, value)
    assert getattr(box, field) == expected


@given(value=st.integers(min_value=INT64_MIN - 2**70, max_value=INT64_MAX + 2**70))
@example(value=INT64_MIN)
@example(value=INT64_MAX)
@example(value=INT64_MIN - 1)
@example(value=INT64_MAX + 1)
def test_int_round_trips_within_int64(box: Codec, value: int) -> None:
    if INT64_MIN <= value <= INT64_MAX:
        box.count = value
        assert box.count == value
        return
    before = box.count
    with pytest.raises(
        OverflowError, match="Codec.count holds a signed 64-bit integer"
    ):
        box.count = value
    assert box.count == before


@given(value=st.floats())
@example(value=-0.0)
@example(value=float("inf"))
@example(value=float("-inf"))
@example(value=float("nan"))
@example(value=sys.float_info.min / 2)
def test_float_round_trips_bit_for_bit(box: Codec, value: float) -> None:
    box.ratio = value
    assert struct.pack("<d", box.ratio) == struct.pack("<d", value)


@given(value=st.booleans())
def test_bool_round_trips(box: Codec, value: bool) -> None:
    box.flag = value
    assert box.flag is value


@given(value=st.sampled_from([0, 1]))
def test_bool_refuses_0_and_1(box: Codec, value: int) -> None:
    before = box.flag
    with pytest.raises(TypeError, match="Codec.flag expects bool, got int"):
        box.flag = value  # type: ignore[assignment] # the int is the point
    assert box.flag is before
