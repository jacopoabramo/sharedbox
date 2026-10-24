from __future__ import annotations

import uuid
from collections.abc import Iterator

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.extra.numpy import from_dtype

from sharedbox import SharedBox

TYPES = [
    np.int8,
    np.int16,
    np.int32,
    np.int64,
    np.uint8,
    np.uint16,
    np.uint32,
    np.uint64,
    np.float16,
    np.float32,
    np.float64,
    np.complex64,
    np.complex128,
    np.bool_,
    np.datetime64,
]


class Every(SharedBox):
    int8: np.int8 = np.int8(0)
    int16: np.int16 = np.int16(0)
    int32: np.int32 = np.int32(0)
    int64: np.int64 = np.int64(0)
    uint8: np.uint8 = np.uint8(0)
    uint16: np.uint16 = np.uint16(0)
    uint32: np.uint32 = np.uint32(0)
    uint64: np.uint64 = np.uint64(0)
    float16: np.float16 = np.float16(0)
    float32: np.float32 = np.float32(0)
    float64: np.float64 = np.float64(0)
    complex64: np.complex64 = np.complex64(0)
    complex128: np.complex128 = np.complex128(0)
    bool: np.bool_ = np.False_
    datetime64: np.datetime64 = np.datetime64("NaT", "ns")


def dtype_of(t: type) -> np.dtype:
    return np.dtype("datetime64[ns]") if t is np.datetime64 else np.dtype(t)


NUMPY_VALUES = st.sampled_from(TYPES).flatmap(
    lambda t: st.tuples(
        st.just("datetime64" if t is np.datetime64 else np.dtype(t).name),
        from_dtype(dtype_of(t)),
    )
)
INT_RANGES = [
    (np.dtype(t).name, np.iinfo(t)) for t in TYPES if np.issubdtype(t, np.integer)
]


@pytest.fixture(scope="module")
def box() -> Iterator[Every]:
    name = f"sbtest-{uuid.uuid4().hex[:16]}"
    with Every.create(name) as created:
        yield created
    Every.unlink(name)


@given(item=NUMPY_VALUES)
def test_numpy_values_round_trip_with_type_and_value(
    box: Every, item: tuple[str, np.generic]
) -> None:
    """Read back every numpy value written to the field of its type, with its type and value unchanged."""
    name, value = item
    setattr(box, name, value)
    got = getattr(box, name)
    assert type(got) is type(value)
    assert np.array_equal(np.array([got]), np.array([value]), equal_nan=True)
    if np.issubdtype(type(value), np.floating):
        assert np.signbit(got) == np.signbit(value)


@given(kind=st.sampled_from(INT_RANGES), value=st.integers(-(2**65), 2**65))
def test_python_ints_fit_their_width_or_overflow(
    box: Every, kind: tuple[str, np.iinfo], value: int
) -> None:
    """Store a Python int that fits an integer field's width and raise OverflowError for one that does not."""
    name, info = kind
    if info.min <= value <= info.max:
        setattr(box, name, value)
        assert getattr(box, name) == value
    else:
        with pytest.raises(OverflowError):
            setattr(box, name, value)
