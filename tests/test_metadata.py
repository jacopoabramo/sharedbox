import math
import queue
import subprocess
import sys
import sysconfig
from collections.abc import Mapping
from dataclasses import InitVar
from types import MappingProxyType
from typing import Any

import pytest
from crossproc import call_in_child, read_and_add_metadata

import sharedbox
from sharedbox import BoxClosedError, MetadataFullError, MetadataGoneError, SharedBox
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


class Stage(SharedBox):
    position: int = 0


class Kept(SharedBox, metadata_versions=2):
    position: int = 0


class Roomy(SharedBox, metadata_capacity=1024):
    position: int = 0


def test_box_metadata_end_to_end(unique_name: str) -> None:
    """Read metadata in another process and see the version it adds signalled here."""
    added: queue.Queue[int] = queue.Queue()
    with Stage.create(unique_name, metadata={"units": "mm"}) as box:
        box.events.metadata.connect(added.put)
        seen = call_in_child(read_and_add_metadata, box, {"units": "um"})
        assert seen == {"units": "mm"}
        assert added.get(timeout=20) == 2
        assert box.metadata == {"units": "um"}
        assert box.metadata_version == 2
        assert box.metadata_version_of(1) == {"units": "mm"}
    Stage.unlink(unique_name)


def test_box_metadata_version_is_reclaimed(unique_name: str) -> None:
    """Raise MetadataGoneError for a version that later adds reclaimed."""
    with Kept.create(unique_name, metadata={"step": 1}) as box:
        for step in (2, 3, 4):
            box.add_metadata({"step": step})
        with pytest.raises(MetadataGoneError):
            box.metadata_version_of(1)
        assert box.metadata == {"step": 4}
    Kept.unlink(unique_name)


def test_a_cached_version_is_gone_once_reclaimed(unique_name: str) -> None:
    """Raise MetadataGoneError for a reclaimed version this box object read before."""
    with Kept.create(unique_name, metadata={"step": 1}) as box:
        assert box.metadata_version_of(1) == {"step": 1}
        for step in (2, 3):
            box.add_metadata({"step": step})
        with pytest.raises(MetadataGoneError):
            box.metadata_version_of(1)
    Kept.unlink(unique_name)


def test_a_class_metadata_capacity_gives_an_area(unique_name: str) -> None:
    """Add a first metadata version to a box created without metadata whose class sets metadata_capacity."""
    with Roomy.create(unique_name) as box:
        assert (box.metadata, box.metadata_version) == (None, 0)
        assert box.add_metadata({"units": "mm"}) == 1
        assert box.metadata == {"units": "mm"}
    Roomy.unlink(unique_name)


def test_box_without_metadata(unique_name: str) -> None:
    """Read no metadata from a box created without it, and refuse to add any."""
    with Stage.create(unique_name) as box:
        assert box.metadata is None
        assert box.metadata_version == 0
        with pytest.raises(MetadataFullError, match="metadata_capacity"):
            box.add_metadata({"units": "mm"})
    Stage.unlink(unique_name)


def test_add_metadata_on_a_closed_box(unique_name: str) -> None:
    """Raise BoxClosedError for reading or adding metadata on a closed box."""
    box = Stage.create(unique_name, metadata={"units": "mm"})
    assert box.metadata == {"units": "mm"}
    box.close()
    with pytest.raises(BoxClosedError):
        box.add_metadata({"units": "um"})
    with pytest.raises(BoxClosedError):
        _ = box.metadata
    Stage.unlink(unique_name)


def test_a_box_created_again_shows_its_own_metadata(unique_name: str) -> None:
    """Read the metadata of the box created again under a name, not of the one before."""
    with Stage.create(unique_name, metadata={"a": 1}) as box:
        assert box.metadata == {"a": 1}
    Stage.unlink(unique_name)
    with Stage.create(unique_name, metadata={"a": 2}) as box:
        assert box.metadata == {"a": 2}
    Stage.unlink(unique_name)


@pytest.mark.parametrize("versions", [1, 65536])
def test_metadata_versions_out_of_range(versions: int) -> None:
    """Refuse a metadata_versions class keyword outside 2 to 65535."""
    with pytest.raises(ValueError, match="metadata_versions"):
        type(
            "Narrow",
            (SharedBox,),
            {"__annotations__": {"x": int}},
            metadata_versions=versions,
        )


@pytest.mark.parametrize("capacity", [-1, 8, 2**32])
def test_metadata_capacity_out_of_range(capacity: int) -> None:
    """Refuse a metadata_capacity class keyword that no area can have."""
    with pytest.raises(ValueError, match="metadata_capacity"):
        type(
            "Small",
            (SharedBox,),
            {"__annotations__": {"x": int}},
            metadata_capacity=capacity,
        )


def test_a_document_longer_than_the_area_is_refused(unique_name: str) -> None:
    """Raise ValueError for a document longer than the box takes, creating or adding."""
    with pytest.raises(ValueError, match="metadata_capacity"):
        Stage.create(unique_name, metadata={"text": "x" * 30000})
    with Stage.create(unique_name, metadata={"units": "mm"}) as box:
        with pytest.raises(ValueError, match="metadata_capacity"):
            box.add_metadata({"text": "x" * 30000})
        assert box.metadata_version == 1
    Stage.unlink(unique_name)


@pytest.mark.parametrize("hint", [int, InitVar[int]], ids=["field", "InitVar"])
@pytest.mark.parametrize("name", ["metadata", "add_metadata"])
def test_a_field_named_metadata_is_refused(name: str, hint: object) -> None:
    """Refuse a field or InitVar named like a metadata member of SharedBox when the class is defined."""
    with pytest.raises(TypeError, match=rf"field name\(s\) {name} clash"):
        type(
            "Clash",
            (SharedBox,),
            {
                "__annotations__": {"x": int, name: hint},
                "__post_init__": lambda self, *initvars: None,
            },
        )
