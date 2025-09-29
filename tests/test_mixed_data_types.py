"""
Test mixed data types support in SharedDict (numpy arrays + built-in types)
"""

import multiprocessing as mp
from typing import Generator

import numpy as np
import pytest

from sharedbox import SharedDict


@pytest.fixture
def mixed_dict() -> Generator[SharedDict, None, None]:
    """Pytest fixture that provides a clean SharedDict instance with automatic cleanup"""
    d = SharedDict(
        "mixed_types_fixture", size=50 * 1024 * 1024, create=True, max_keys=128
    )
    try:
        yield d
    finally:
        d.close()
        d.unlink()


@pytest.fixture
def concurrent_dict() -> Generator[SharedDict, None, None]:
    """Pytest fixture for concurrent access tests"""
    d = SharedDict(
        "mixed_concurrent_fixture", size=100 * 1024 * 1024, create=True, max_keys=256
    )
    try:
        yield d
    finally:
        d.close()
        d.unlink()


@pytest.fixture
def performance_dict() -> Generator[SharedDict, None, None]:
    """Pytest fixture for performance tests"""
    d = SharedDict(
        "mixed_perf_fixture", size=100 * 1024 * 1024, create=True, max_keys=128
    )
    try:
        yield d
    finally:
        d.close()
        d.unlink()


@pytest.fixture
def compatibility_dict() -> Generator[SharedDict, None, None]:
    """Pytest fixture for compatibility tests"""
    d = SharedDict(
        "compat_test_fixture", size=50 * 1024 * 1024, create=True, max_keys=64
    )
    try:
        yield d
    finally:
        d.close()
        d.unlink()


@pytest.mark.skip(reason="Flaky test, needs investigation")
def test_mixed_data_types(mixed_dict: SharedDict) -> None:
    """Test storing and retrieving mixed numpy arrays and built-in Python types"""
    d = mixed_dict

    # Test data with mixed types
    test_data = {
        # Numpy arrays
        "small_array": np.array([1, 2, 3, 4, 5]),
        "float_array": np.random.rand(10, 10).astype(np.float32),
        "int_matrix": np.random.randint(0, 100, (5, 5), dtype=np.int32),
        "bool_array": np.array([True, False, True, False], dtype=bool),
        # Built-in Python types
        "string_key": "hello world",
        "integer_key": 42,
        "float_key": 3.14159,
        "list_key": [1, 2, 3, "mixed", 4.5],
        "dict_key": {"nested": "dictionary", "count": 123},
        "tuple_key": (1, "tuple", 3.14, [4, 5, 6]),
        "bool_key": True,
        "none_key": None,
    }

    # Store all test data
    for key, value in test_data.items():
        d[key] = value

    # Verify all data can be retrieved correctly
    for key, original_value in test_data.items():
        retrieved_value = d[key]

        if isinstance(original_value, np.ndarray):
            # For numpy arrays, use numpy testing
            assert isinstance(retrieved_value, np.ndarray)
            assert retrieved_value.dtype == original_value.dtype
            assert retrieved_value.shape == original_value.shape
            np.testing.assert_array_equal(retrieved_value, original_value)
        else:
            # For other types, use regular equality
            assert retrieved_value == original_value, (
                f"Mismatch for key {key}: {retrieved_value} != {original_value}"
            )

    # Test mixed operations
    assert len(d) == len(test_data)
    assert all(key in d for key in test_data.keys())


def test_mixed_data_concurrent_access(concurrent_dict: SharedDict) -> None:
    """Test concurrent access to mixed data types"""
    d = concurrent_dict

    # Initialize with some shared data
    shared_config = {
        "numpy_reference": np.random.rand(50, 50).astype(np.float64),
        "shared_counter": 0,
        "shared_settings": {"workers": 4, "enabled": True},
        "shared_results": [],
    }

    for key, value in shared_config.items():
        d[key] = value

    num_workers = 3
    iterations = 8

    with mp.Pool(num_workers) as pool:
        results = []
        for worker_id in range(num_workers):
            result = pool.apply_async(
                mixed_data_worker, (worker_id, "mixed_concurrent_fixture", iterations)
            )
            results.append(result)

        for result in results:
            assert result.get(timeout=30)

    # Verify mixed data integrity
    total_numpy_arrays = 0
    total_builtin_objects = 0

    for key in d.keys():
        value = d[key]
        if isinstance(value, np.ndarray):
            total_numpy_arrays += 1
            assert value.shape[0] > 0  # Basic validity check
        else:
            total_builtin_objects += 1

    # Should have arrays from each worker plus shared data
    expected_arrays = num_workers * iterations + 1  # +1 for shared numpy_reference
    assert total_numpy_arrays >= expected_arrays
    assert total_builtin_objects >= 3  # At least the shared built-in objects


def mixed_data_worker(worker_id: int, dict_name: str, iterations: int) -> bool:
    """Worker function that manipulates mixed data types"""
    d = SharedDict(dict_name, create=False)

    for i in range(iterations):
        # Create and store numpy array
        numpy_key = f"worker_{worker_id}_array_{i}"
        arr = np.random.rand(20, 20).astype(np.float32) * (worker_id + 1)
        d[numpy_key] = arr

        # Create and store built-in Python object
        builtin_key = f"worker_{worker_id}_data_{i}"
        builtin_data = {
            "worker_id": worker_id,
            "iteration": i,
            "timestamp": i * 0.1,
            "results": [worker_id * 10 + j for j in range(5)],
            "success": True,
        }
        d[builtin_key] = builtin_data

        # Verify numpy array
        retrieved_arr = d[numpy_key]
        assert isinstance(retrieved_arr, np.ndarray)
        np.testing.assert_array_equal(retrieved_arr, arr)

        # Verify built-in data
        retrieved_data = d[builtin_key]
        assert retrieved_data == builtin_data

        # Read shared numpy reference
        if "numpy_reference" in d:
            shared_arr = d["numpy_reference"]
            assert isinstance(shared_arr, np.ndarray)
            assert shared_arr.shape == (50, 50)

        # Read shared built-in data
        if "shared_settings" in d:
            settings = d["shared_settings"]
            assert isinstance(settings, dict)
            assert "workers" in settings

    d.close()
    return True


def test_mixed_data_types_performance(performance_dict: SharedDict) -> None:
    """Test performance characteristics of mixed data operations"""
    d = performance_dict

    # Test different data type combinations
    test_scenarios = [
        # (numpy_arrays, builtin_objects, description)
        (10, 10, "Equal mix"),
        (20, 5, "Numpy heavy"),
        (5, 20, "Built-in heavy"),
        (50, 1, "Almost all numpy"),
        (1, 50, "Almost all built-in"),
    ]

    for num_numpy, num_builtin, description in test_scenarios:
        # Clear dictionary for each scenario
        for key in list(d.keys()):
            del d[key]

        # Store numpy arrays
        for i in range(num_numpy):
            key = f"numpy_{i}"
            arr = np.random.rand(10, 10).astype(np.float32)
            d[key] = arr

        # Store built-in objects
        for i in range(num_builtin):
            key = f"builtin_{i}"
            obj = {
                "id": i,
                "data": [j * 2 for j in range(10)],
                "metadata": {"type": "test", "valid": True},
            }
            d[key] = obj

        # Verify mixed retrieval works correctly
        numpy_count = 0
        builtin_count = 0

        for key in d.keys():
            value = d[key]
            if isinstance(value, np.ndarray):
                numpy_count += 1
                assert value.shape == (10, 10)
            else:
                builtin_count += 1
                assert isinstance(value, dict)
                assert "id" in value

        assert numpy_count == num_numpy
        assert builtin_count == num_builtin
        assert len(d) == num_numpy + num_builtin


def test_numpy_builtin_serialization_compatibility(
    compatibility_dict: SharedDict,
) -> None:
    """Test that numpy arrays and built-in types don't interfere with each other's serialization"""
    d = compatibility_dict

    # Test separate storage and retrieval to ensure no interference
    # Store numpy array first
    numpy_data = np.array([[1, 2], [3, 4]], dtype=np.int32)
    d["numpy_test"] = numpy_data

    # Store built-in data
    builtin_data = {"nested": {"deep": [1, 2, 3]}, "count": 42, "valid": True}
    d["builtin_test"] = builtin_data

    # Store more complex built-in structure
    complex_builtin = {
        "metadata": {"type": "test", "version": "1.0"},
        "data_list": [1, 2, 3, "mixed", {"inner": "dict"}],
        "tuples": (1, 2, "tuple_data", [4, 5, 6]),
    }
    d["complex_builtin"] = complex_builtin

    # Retrieve and verify numpy data wasn't affected
    retrieved_numpy = d["numpy_test"]
    assert isinstance(retrieved_numpy, np.ndarray)
    assert retrieved_numpy.dtype == numpy_data.dtype
    np.testing.assert_array_equal(retrieved_numpy, numpy_data)

    # Retrieve and verify built-in data wasn't affected
    retrieved_builtin = d["builtin_test"]
    assert retrieved_builtin == builtin_data

    # Retrieve and verify complex built-in data
    retrieved_complex = d["complex_builtin"]
    assert retrieved_complex == complex_builtin

    # Test interleaved operations
    for i in range(5):
        # Store numpy
        arr_key = f"array_{i}"
        arr = np.random.rand(5, 5).astype(np.float32)
        d[arr_key] = arr

        # Store built-in
        obj_key = f"object_{i}"
        obj = {"id": i, "values": list(range(i * 10, (i + 1) * 10))}
        d[obj_key] = obj

        # Verify both work correctly
        retrieved_arr = d[arr_key]
        retrieved_obj = d[obj_key]

        assert isinstance(retrieved_arr, np.ndarray)
        np.testing.assert_array_equal(retrieved_arr, arr)
        assert retrieved_obj == obj
