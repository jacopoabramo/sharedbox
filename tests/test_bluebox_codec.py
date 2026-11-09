import enum

import numpy as np
import pytest

from sharedbox.bluebox._codec import decode_document, encode_document, split_parts


class Kind(enum.StrEnum):
    TEXT = "text"


def test_round_trip_keeps_arrays() -> None:
    """Return arrays and numpy scalars of the same dtype and shape."""
    configuration = {
        "matrix": np.arange(6, dtype="<u2").reshape(2, 3),
        "gain": np.float32(1.5),
        "series": np.array([1, 2, 3], dtype=np.int64),
        "nested": [1, [2.5, "x"], {"k": None}],
        "label": "cam",
    }
    name, doc = decode_document(
        encode_document("descriptor", {"configuration": configuration})
    )

    assert name == "descriptor"
    result = doc["configuration"]
    assert isinstance(result, dict)
    assert result["matrix"].dtype == np.uint16
    assert result["matrix"].shape == (2, 3)
    np.testing.assert_array_equal(result["matrix"], configuration["matrix"])
    assert result["gain"].dtype == np.float32
    assert result["gain"] == np.float32(1.5)
    assert result["series"].dtype == np.int64
    assert result["series"].shape == (3,)
    assert result["nested"] == configuration["nested"]
    assert result["label"] == "cam"


def test_big_endian_array_comes_back_equal() -> None:
    """Write a big-endian array by value, not by bytes."""
    source = np.arange(4, dtype=">i4")
    _, doc = decode_document(encode_document("event", {"v": source}))

    result = doc["v"]
    assert isinstance(result, np.ndarray)
    np.testing.assert_array_equal(result, source)
    assert result.dtype == np.int32


def test_enum_member_becomes_its_value() -> None:
    """Write an enum member as its value."""
    _, doc = decode_document(encode_document("start", {"kind": Kind.TEXT}))

    assert doc["kind"] == "text"
    assert type(doc["kind"]) is str


def test_unknown_type_names_the_key() -> None:
    """Raise a TypeError naming the document and the dotted key path."""
    with pytest.raises(TypeError, match="start") as info:
        encode_document("start", {"a": {"b": object()}})

    assert "a.b" in str(info.value)


def test_unsupported_dtype_names_the_key() -> None:
    """Raise a TypeError naming the key of an array with no typed array tag."""
    with pytest.raises(TypeError, match="a.1") as info:
        encode_document("start", {"a": [1, np.array([True])]})

    assert "start" in str(info.value)


@pytest.mark.parametrize("size", [0, 1, 16384, 16385, 100000])
def test_split_parts_covers_every_byte(size: int) -> None:
    """Return parts that join to the input, all but the last of the part size."""
    data = bytes(range(256)) * (size // 256 + 1)
    data = data[:size]

    parts = split_parts(data, 16384)

    assert b"".join(parts) == data
    assert all(len(part) == 16384 for part in parts[:-1])
    assert len(parts[-1]) <= 16384
    if size == 0:
        assert parts == [b""]
