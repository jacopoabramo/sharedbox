import math
import subprocess
import sys
import sysconfig
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import pytest

import sharedbox
from sharedbox import SharedBox
from sharedbox._metadata import DocumentCache, decode, encode

DOCUMENT = {
    "none": None,
    "yes": True,
    "no": False,
    "small": 7,
    "negative": -5,
    "largest": 2**64 - 1,
    "smallest": -(2**64),
    "pi": 3.14159,
    "text": "café",
    "raw": b"\x00\xff",
    "list": [1, [2, {"deep": (3, 4)}]],
    "map": {"inner": {"leaf": "x"}},
}


def thawed(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thawed(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thawed(item) for item in value]
    return value


def expected(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: expected(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [expected(item) for item in value]
    return value


def test_documents_round_trip() -> None:
    """Decode what was encoded, with maps read-only and arrays as tuples."""
    decoded = decode(encode(DOCUMENT))
    assert isinstance(decoded, MappingProxyType)
    assert isinstance(decoded["list"], tuple)
    assert thawed(decoded) == expected(DOCUMENT)


@pytest.mark.parametrize("number", [math.inf, -math.inf, math.nan, -0.0])
def test_floats_round_trip(number: float) -> None:
    """Keep NaN, infinities and the sign of zero."""
    result = decode(encode(number))
    assert math.copysign(1, result) == math.copysign(1, number)
    assert (math.isnan(result) and math.isnan(number)) or result == number


@pytest.mark.parametrize("value", [None, True, False, 0, "", b""])
def test_scalars_round_trip(value: object) -> None:
    """Round trip a document that is a single value, keeping its type."""
    result = decode(encode(value))
    assert result == value
    assert type(result) is type(value)


def test_empty_containers_round_trip() -> None:
    """Round trip an empty array and an empty map."""
    assert decode(encode([])) == ()
    assert decode(encode({})) == {}


def test_equal_values_encode_equally() -> None:
    """Give equal bytes for maps with the same items in another order."""
    assert encode({"a": 1, "bb": [1, 2], "c": None}) == encode(
        {"c": None, "bb": (1, 2), "a": 1}
    )


def test_a_decoded_map_can_be_added_again() -> None:
    """Accept the read-only mapping a read returned."""
    decoded = decode(encode(DOCUMENT))
    assert encode(decoded) == encode(DOCUMENT)


def check_subset(data: bytes, at: int = 0, depth: int = 0) -> int:
    """Walk one item, failing on a tag, an indefinite length, a simple value outside the subset, a key that is not text or nesting past 32."""
    major, extra = data[at] >> 5, data[at] & 31
    at += 1
    assert major != 6, "tag"
    assert extra < 28, "reserved or indefinite length"
    if major == 7:
        assert extra in (20, 21, 22, 25, 26, 27)
        return at + {25: 2, 26: 4, 27: 8}.get(extra, 0)
    width = {24: 1, 25: 2, 26: 4, 27: 8}.get(extra, 0)
    count = extra if extra < 24 else int.from_bytes(data[at : at + width], "big")
    at += width
    if major in (2, 3):
        return at + count
    if major in (4, 5):
        assert depth < 32, "nesting"
    if major == 4:
        for _ in range(count):
            at = check_subset(data, at, depth + 1)
    elif major == 5:
        for _ in range(count):
            assert data[at] >> 5 == 3, "map key is not text"
            at = check_subset(data, at, depth + 1)
            at = check_subset(data, at, depth + 1)
    return at


def test_documents_stay_in_the_checked_subset() -> None:
    """Emit no tag, no indefinite length and no simple value outside the subset."""
    documents: list[object] = [
        DOCUMENT,
        2**64 - 1,
        -(2**64),
        math.inf,
        [[], {}],
        b"",
        "",
    ]
    for value in documents:
        data = encode(value)
        assert check_subset(data) == len(data)
    assert check_subset(encode(nested(32))) > 0
    assert encode(None) == bytes([0xF6])
    assert encode(True) == bytes([0xF5])
    assert encode(False) == bytes([0xF4])
    assert encode(math.nan)[0] == 0xF9
    for value in (2**64 - 1, -(2**64), 2**63, -1):
        assert encode(value)[0] >> 5 in (0, 1)


@pytest.mark.parametrize(
    "value",
    [{1, 2}, {1: "a"}, object(), bytearray(b"x"), {"a": {1, 2}}, [object()], 1j],
)
def test_wrong_types_raise_type_error(value: object) -> None:
    """Refuse a type outside the document types."""
    with pytest.raises(TypeError):
        encode(value)


def nested(levels: int) -> object:
    value: object = 1
    for _ in range(levels):
        value = [value]
    return value


def test_out_of_range_raises_value_error() -> None:
    """Refuse an integer beyond 64 bits and nesting deeper than 32."""
    for number in (2**64, -(2**64) - 1):
        with pytest.raises(ValueError):
            encode(number)
    encode(nested(32))
    with pytest.raises(ValueError):
        encode(nested(33))
    cycle: list[object] = []
    cycle.append(cycle)
    with pytest.raises(ValueError):
        encode(cycle)


def test_decoded_values_are_read_only() -> None:
    """Refuse item assignment on a decoded map and on a map inside it."""
    decoded = decode(encode({"outer": {"inner": 1}}))
    with pytest.raises(TypeError):
        decoded["outer"] = 2
    with pytest.raises(TypeError):
        decoded["outer"]["inner"] = 2


def test_document_cache_reads_once_per_version() -> None:
    """Call the reader once per creation id and version."""
    cache = DocumentCache()
    calls: list[int] = []

    def read() -> bytes:
        calls.append(1)
        return encode({"n": len(calls)})

    first = cache.get(1, 1, read)
    assert cache.get(1, 1, read) is first
    assert cache.get(1, 2, read) is not first
    assert cache.get(2, 1, read) is not first
    assert len(calls) == 3


def test_document_cache_is_bounded() -> None:
    """Drop the oldest versions once many are cached."""
    cache = DocumentCache()
    for version in range(200):
        cache.get(1, version, lambda: encode(None))
    calls: list[int] = []

    def read() -> bytes:
        calls.append(1)
        return encode(None)

    cache.get(1, 0, read)
    assert calls


def test_errors_are_exported() -> None:
    """Derive the two errors from the builtin classes callers can catch."""
    assert issubclass(sharedbox.MetadataFullError, RuntimeError)
    assert issubclass(sharedbox.MetadataGoneError, LookupError)


def test_a_box_subclasses_a_parameterised_base(unique_name: str) -> None:
    """Create a box whose class names its metadata type."""

    class Info(dict[str, Any]):
        pass

    class Motor(SharedBox[Info], name=unique_name):
        speed: int

    with Motor(3) as motor:
        assert motor.speed == 3
    Motor.unlink()


@pytest.mark.skipif(
    not sysconfig.get_config_var("Py_GIL_DISABLED"),
    reason="needs a free-threaded build",
)
def test_importing_sharedbox_keeps_the_gil_off() -> None:
    """Keep the GIL disabled after a fresh process imports sharedbox."""
    code = "import sharedbox, sys; sys.exit(int(sys._is_gil_enabled()))"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
