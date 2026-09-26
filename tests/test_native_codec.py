import array
import enum
import math
import struct

import pytest

from sharedbox._layout import NativeField
from sharedbox._native import Segment, check

BOOL, INT, FLOAT, STR, BYTES = range(5)
FIELDS = [
    NativeField(0, 8, INT),
    NativeField(8, 8, FLOAT),
    NativeField(16, 10, STR),
    NativeField(32, 3, BYTES),
    NativeField(40, 1, BOOL),
]
NAMES = ["count", "ratio", "label", "blob", "flag"]
RECORD_SIZE = 48
SCHEMA = 0xC0DEC


class Colour(enum.IntEnum):
    RED = 1


def create(name: str, values: list[tuple[int, object]] | None = None) -> Segment:
    return Segment.create(name, FIELDS, NAMES, RECORD_SIZE, SCHEMA, 1.0, values or [])


@pytest.mark.parametrize(
    ("index", "value"),
    [
        (0, -(2**63)),
        (0, 2**63 - 1),
        (0, Colour.RED),
        (1, 0.25),
        (1, -0.0),
        (1, math.inf),
        (2, "héllo"),
        (3, b"\x00\x01"),
        (3, bytearray(b"ab")),
        (3, memoryview(b"xyz")),
        (4, True),
    ],
)
def test_values_round_trip(unique_name: str, index: int, value: object) -> None:
    segment = create(unique_name)
    segment.set([(index, value)])
    got = segment.get(index)
    if isinstance(value, float):
        assert struct.pack("<d", got) == struct.pack("<d", value)
    elif isinstance(value, (bytearray, memoryview)):
        assert got == bytes(value)
    else:
        assert got == value
    segment.close()


def test_nan_round_trips_bit_for_bit(unique_name: str) -> None:
    segment = create(unique_name)
    nan = struct.unpack("<d", struct.pack("<Q", 0x7FF8_0000_0000_0001))[0]
    segment.set([(1, nan)])
    assert struct.pack("<d", segment.get(1)) == struct.pack("<d", nan)
    segment.close()


def test_strided_memoryview_is_stored_in_order(unique_name: str) -> None:
    segment = create(unique_name)
    segment.set([(3, memoryview(b"abcdef")[::2])])
    assert segment.get(3) == b"ace"
    segment.close()


def test_float_field_accepts_int(unique_name: str) -> None:
    segment = create(unique_name)
    segment.set([(1, 3)])
    got = segment.get(1)
    assert got == 3.0 and isinstance(got, float)
    segment.close()


@pytest.mark.parametrize(
    ("index", "value", "error", "text"),
    [
        (0, True, TypeError, "count expects int, got bool"),
        (0, 2**63, OverflowError, "count holds a signed 64-bit integer"),
        (0, 1.5, TypeError, "count expects int, got float"),
        (1, "1", TypeError, "ratio expects float, got str"),
        (1, 2**1024, OverflowError, "ratio holds a 64-bit float"),
        (2, b"x", TypeError, "label expects str, got bytes"),
        (
            2,
            "é" * 6,
            ValueError,
            "label holds at most 10 bytes; the value encodes to 12",
        ),
        (2, "\ud800", UnicodeEncodeError, ""),
        (3, "x", TypeError, "blob expects bytes, got str"),
        (3, b"abcd", ValueError, "blob holds at most 3 bytes"),
        (3, array.array("b", [1]), TypeError, "blob expects bytes, got array"),
        (4, 1, TypeError, "flag expects bool, got int"),
    ],
)
def test_bad_values_are_refused_and_change_nothing(
    unique_name: str, index: int, value: object, error: type[Exception], text: str
) -> None:
    segment = create(unique_name, [(0, 7), (2, "kept")])
    with pytest.raises(error, match=text or None):
        segment.set([(2, "new"), (index, value)])
    assert segment.get(0) == 7
    assert segment.get(2) == "kept"
    segment.close()


def test_check_matches_set() -> None:
    check(INT, 8, "count", 5)
    with pytest.raises(TypeError, match="count expects int, got str"):
        check(INT, 8, "count", "5")
    with pytest.raises(ValueError, match="label holds at most 10 bytes"):
        check(STR, 10, "label", "x" * 11)


def test_initial_values_are_visible_on_attach(unique_name: str) -> None:
    segment = create(unique_name, [(0, 42), (2, "ready")])
    other = Segment.attach(unique_name, NAMES, SCHEMA, 1.0)
    assert other.get_all() == [42, 0.0, "ready", b"", False]
    other.close()
    segment.close()


def test_get_versioned_pairs_value_and_version(unique_name: str) -> None:
    segment = create(unique_name)
    assert segment.get_versioned(0) == (0, 0)
    segment.set([(0, 9)])
    assert segment.get_versioned(0) == (1, 9)
    segment.close()


def test_invalid_utf8_reads_with_replacement(unique_name: str) -> None:
    segment = create(unique_name)
    segment._write([(2, b"\xff\xfe")])
    assert segment.get(2) == "\ufffd\ufffd"
    segment.close()
