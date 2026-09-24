"""
Performance benchmarks for SharedDict, run with pytest-codspeed.

Run locally with:
    pytest benchmarks --codspeed
"""

import itertools
import os
from collections.abc import Iterator

import numpy as np
import pytest

from sharedbox import SharedDict
from sharedbox.utils import LockTuner, SegmentSizer

pytestmark = pytest.mark.benchmark

SEGMENT_SIZE = 64 * 1024 * 1024
_counter = itertools.count()


def _segment_name(prefix: str) -> str:
    """Return a unique shared memory segment name for this process."""
    return f"bench_{prefix}_{os.getpid()}_{next(_counter)}"


@pytest.fixture
def shared_dict() -> Iterator[SharedDict]:
    d = SharedDict(_segment_name("dict"), size=SEGMENT_SIZE, create=True, max_keys=2048)
    yield d
    d.close()
    d.unlink()


def _populate(d: SharedDict, n: int) -> list[str]:
    keys = [f"key_{i}" for i in range(n)]
    for i, key in enumerate(keys):
        d[key] = {"id": i, "name": f"item_{i}", "tags": ["a", "b", "c"]}
    return keys


# --- Lifecycle ---------------------------------------------------------------


def test_create_close_unlink(benchmark) -> None:
    @benchmark
    def run() -> None:
        d = SharedDict(_segment_name("lifecycle"), size=4 * 1024 * 1024, create=True)
        d.close()
        d.unlink()


def test_create_with_initial_data(benchmark) -> None:
    data = {f"key_{i}": {"id": i, "value": i * 1.5} for i in range(100)}

    @benchmark
    def run() -> None:
        d = SharedDict(_segment_name("init"), data, size=8 * 1024 * 1024, create=True)
        d.close()
        d.unlink()


# --- Python object values (pickle path) --------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(42, id="int"),
        pytest.param("hello world" * 10, id="str"),
        pytest.param(list(range(100)), id="list"),
        pytest.param({"a": 1, "b": [1, 2, 3], "c": "text"}, id="dict"),
    ],
)
def test_setitem_object(benchmark, shared_dict: SharedDict, value: object) -> None:
    @benchmark
    def run() -> None:
        shared_dict["key"] = value


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(42, id="int"),
        pytest.param("hello world" * 10, id="str"),
        pytest.param(list(range(100)), id="list"),
        pytest.param({"a": 1, "b": [1, 2, 3], "c": "text"}, id="dict"),
    ],
)
def test_getitem_object(benchmark, shared_dict: SharedDict, value: object) -> None:
    shared_dict["key"] = value
    result = benchmark(shared_dict.__getitem__, "key")
    assert result == value


def test_bulk_insert_100(benchmark, shared_dict: SharedDict) -> None:
    items = [(f"key_{i}", {"id": i, "name": f"item_{i}"}) for i in range(100)]

    @benchmark
    def run() -> None:
        for key, value in items:
            shared_dict[key] = value


def test_bulk_read_100(benchmark, shared_dict: SharedDict) -> None:
    keys = _populate(shared_dict, 100)

    @benchmark
    def run() -> None:
        for key in keys:
            shared_dict[key]


def test_insert_delete(benchmark, shared_dict: SharedDict) -> None:
    @benchmark
    def run() -> None:
        shared_dict["tmp"] = "value"
        del shared_dict["tmp"]


# --- Lookup / iteration ------------------------------------------------------


def test_contains(benchmark, shared_dict: SharedDict) -> None:
    keys = _populate(shared_dict, 500)

    @benchmark
    def run() -> None:
        for key in keys[:100]:
            key in shared_dict
        "missing" in shared_dict


def test_get_with_default(benchmark, shared_dict: SharedDict) -> None:
    _populate(shared_dict, 100)

    @benchmark
    def run() -> None:
        shared_dict.get("key_50")
        shared_dict.get("missing", None)


def test_len(benchmark, shared_dict: SharedDict) -> None:
    _populate(shared_dict, 500)
    assert benchmark(len, shared_dict) == 500


def test_keys(benchmark, shared_dict: SharedDict) -> None:
    _populate(shared_dict, 500)
    assert len(benchmark(shared_dict.keys)) == 500


def test_values(benchmark, shared_dict: SharedDict) -> None:
    _populate(shared_dict, 500)
    assert len(benchmark(shared_dict.values)) == 500


def test_items(benchmark, shared_dict: SharedDict) -> None:
    _populate(shared_dict, 500)
    assert len(benchmark(shared_dict.items)) == 500


def test_get_stats(benchmark, shared_dict: SharedDict) -> None:
    _populate(shared_dict, 500)
    benchmark(shared_dict.get_stats)


# --- NumPy values (native serialization path) --------------------------------


@pytest.mark.parametrize(
    "shape,dtype",
    [
        pytest.param((100,), np.float64, id="float64-100"),
        pytest.param((1000, 100), np.float32, id="float32-1000x100"),
        pytest.param((256, 256, 3), np.uint8, id="uint8-256x256x3"),
    ],
)
def test_setitem_numpy(benchmark, shared_dict: SharedDict, shape, dtype) -> None:
    arr = np.arange(np.prod(shape)).reshape(shape).astype(dtype)

    @benchmark
    def run() -> None:
        shared_dict["array"] = arr


@pytest.mark.parametrize(
    "shape,dtype",
    [
        pytest.param((100,), np.float64, id="float64-100"),
        pytest.param((1000, 100), np.float32, id="float32-1000x100"),
        pytest.param((256, 256, 3), np.uint8, id="uint8-256x256x3"),
    ],
)
def test_getitem_numpy(benchmark, shared_dict: SharedDict, shape, dtype) -> None:
    arr = np.arange(np.prod(shape)).reshape(shape).astype(dtype)
    shared_dict["array"] = arr
    result = benchmark(shared_dict.__getitem__, "array")
    assert np.array_equal(result, arr)


def test_setitem_numpy_non_contiguous(benchmark, shared_dict: SharedDict) -> None:
    arr = np.arange(200 * 200, dtype=np.float64).reshape(200, 200).T

    @benchmark
    def run() -> None:
        shared_dict["array"] = arr


# --- Sizing utilities --------------------------------------------------------


def test_segment_sizer_analyze_sample_data(benchmark) -> None:
    keys = [f"key_{i}" for i in range(200)]
    values = [{"id": i, "payload": list(range(10))} for i in range(200)]
    benchmark(SegmentSizer.analyze_sample_data, keys, values)


def test_lock_tuner_recommend_lock_count(benchmark) -> None:
    @benchmark
    def run() -> None:
        for n in (1_000, 100_000, 10_000_000):
            LockTuner.recommend_lock_count(n, write_concurrency=8)
