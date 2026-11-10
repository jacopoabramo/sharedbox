import enum

import numpy as np
import pydantic
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


class Table(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)

    counts: np.ndarray
    labels: list[str]
    kind: Kind


def test_model_becomes_a_dict_of_its_fields() -> None:
    """Write a pydantic model as a dict of its fields, arrays and enum members as elsewhere."""
    table = Table(
        counts=np.array([1, 2], dtype=np.int32), labels=["a", "b"], kind=Kind.TEXT
    )

    _, doc = decode_document(encode_document("descriptor", {"table": table}))

    result = doc["table"]
    assert isinstance(result, dict)
    assert result.keys() == {"counts", "labels", "kind"}
    assert result["counts"].dtype == np.int32
    np.testing.assert_array_equal(result["counts"], [1, 2])
    assert result["labels"] == ["a", "b"]
    assert result["kind"] == "text"


def test_unknown_type_names_the_key() -> None:
    """Raise a TypeError naming the document and the dotted key path."""
    with pytest.raises(TypeError, match="start") as info:
        encode_document("start", {"a": {"b": object()}})

    assert "a.b" in str(info.value)


@pytest.mark.parametrize(
    "value",
    [
        np.array([[True, False], [False, True]]),
        np.array(["ab", "cdef", ""], dtype="<U6"),
        np.array([b"ab", b"c"], dtype="S3"),
        np.array([1 + 2j, 3 - 1j], dtype=np.complex64),
        np.array([1.5], dtype=np.longdouble),
    ],
    ids=["bool", "U6", "S3", "complex64", "longdouble"],
)
def test_array_without_a_typed_array_tag_round_trips(value: np.ndarray) -> None:
    """Return an array of the same dtype, shape and values."""
    _, doc = decode_document(encode_document("event", {"v": value}))

    result = doc["v"]
    assert isinstance(result, np.ndarray)
    assert result.dtype == value.dtype
    assert result.shape == value.shape
    np.testing.assert_array_equal(result, value)


def test_key_order_is_kept() -> None:
    """Return every dict with its keys in the order they were sent."""
    data_keys = {"zeta": {"y": 1, "b": 2}, "alpha_long_name": {}, "b": {}}
    _, doc = decode_document(encode_document("descriptor", {"data_keys": data_keys}))

    assert list(doc["data_keys"]) == ["zeta", "alpha_long_name", "b"]
    assert list(doc["data_keys"]["zeta"]) == ["y", "b"]


def test_bool_scalar_round_trips() -> None:
    """Return a numpy bool scalar for a numpy bool scalar."""
    _, doc = decode_document(encode_document("event", {"v": np.bool_(True)}))

    assert type(doc["v"]) is np.bool_
    assert doc["v"]


def test_object_array_names_the_key() -> None:
    """Raise a TypeError naming the key of an array that cannot be written."""
    with pytest.raises(TypeError, match="a.1") as info:
        encode_document("start", {"a": [1, np.array([object()])]})

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
