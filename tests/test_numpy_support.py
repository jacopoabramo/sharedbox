"""
Test numpy array support in SharedDict
"""

import multiprocessing as mp
import time

import numpy as np

from sharedbox import SharedDict


def test_simple_numpy() -> None:
    """Test very basic numpy functionality"""
    d = SharedDict("simple_numpy", size=10 * 1024 * 1024, create=True)

    # Simple test
    arr = np.array([1, 2, 3, 4, 5])
    d["simple_array"] = arr
    retrieved = d["simple_array"]

    assert isinstance(retrieved, np.ndarray)
    assert arr.dtype == retrieved.dtype
    assert np.array_equal(arr, retrieved)

    d.close()
    d.unlink()


def test_numpy_serialization() -> None:
    """Test numpy array serialization and deserialization"""
    d = SharedDict("test_numpy", size=50 * 1024 * 1024, create=True, max_keys=64)

    # Test different numpy array types
    test_cases = [
        # (name, array)
        ("int32_1d", np.array([1, 2, 3, 4, 5], dtype=np.int32)),
        ("float64_2d", np.random.rand(10, 5).astype(np.float64)),
        ("uint8_3d", np.random.randint(0, 256, (4, 3, 2), dtype=np.uint8)),
        ("complex128", np.array([1 + 2j, 3 + 4j, 5 + 6j], dtype=np.complex128)),
        ("bool_array", np.array([True, False, True, False], dtype=bool)),
        ("large_array", np.random.rand(1000, 100).astype(np.float32)),
    ]

    for name, original_array in test_cases:
        # Store and retrieve array
        d[name] = original_array
        retrieved_array = d[name]

        # Verify correctness
        assert isinstance(retrieved_array, np.ndarray), (
            f"Retrieved object is not ndarray: {type(retrieved_array)}"
        )
        assert retrieved_array.dtype == original_array.dtype, (
            f"Dtype mismatch: {retrieved_array.dtype} != {original_array.dtype}"
        )
        assert retrieved_array.shape == original_array.shape, (
            f"Shape mismatch: {retrieved_array.shape} != {original_array.shape}"
        )

        # Check data equality
        np.testing.assert_array_equal(retrieved_array, original_array)

    d.close()
    d.unlink()


def numpy_worker(dict_name: str, array_name: str, iterations: int) -> bool:
    """Worker function for multiprocessing tests"""
    d = SharedDict(dict_name, size=200 * 1024 * 1024, create=False)

    for i in range(iterations):
        # Read the array
        arr = d[array_name]

        # Simple computation to ensure we're actually using the data
        result = np.sum(arr) + i

        # Store a result (using a unique key per worker)
        worker_id = mp.current_process().pid
        d[f"result_{worker_id}_{i}"] = result

    return True


def test_numpy_multiprocessing() -> None:
    """Test numpy arrays with multiple processes"""
    # Create shared dict and store a test array
    d = SharedDict("numpy_mp_test", size=200 * 1024 * 1024, create=True, max_keys=256)

    # Create a smaller test array to avoid memory issues
    test_array = np.random.rand(500, 500).astype(np.float32)  # About 1MB
    d["test_array"] = test_array

    # Test with multiple processes
    num_processes = 2
    iterations = 5

    with mp.Pool(num_processes) as pool:
        # Start workers
        results = []
        for _ in range(num_processes):
            result = pool.apply_async(
                numpy_worker, ("numpy_mp_test", "test_array", iterations)
            )
            results.append(result)

        # Wait for completion
        for result in results:
            assert result.get(timeout=30)  # 30 second timeout

    d.close()
    d.unlink()


def test_numpy_performance() -> None:
    """Test numpy serialization performance"""
    d = SharedDict("numpy_benchmark", size=100 * 1024 * 1024, create=True)

    # Test different array sizes
    sizes = [
        (100, 100),  # 40KB
        (500, 500),  # 1MB
        (1000, 1000),  # 4MB
    ]

    for shape in sizes:
        array = np.random.rand(*shape).astype(np.float32)

        # Test multiple operations for consistency
        n_ops = 5
        for i in range(n_ops):
            # Store and retrieve
            d[f"bench_array_{i}"] = array
            retrieved = d[f"bench_array_{i}"]

            # Verify correctness
            np.testing.assert_array_equal(array, retrieved)

    d.close()
    d.unlink()


def concurrent_numpy_worker(worker_id: int, dict_name: str, iterations: int) -> bool:
    """Worker function for concurrent numpy access testing"""
    d = SharedDict(dict_name, create=False)

    for i in range(iterations):
        arr = np.random.rand(50, 50).astype(np.float32) * (worker_id + 1)
        key = f"worker_{worker_id}_array_{i}"

        d[key] = arr

        # while concurrently trying to read it back,
        # we might incur in some race conditions...
        # although it shouldn't happen; for now,
        # we add some retry logic
        max_retries = 3
        for retry in range(max_retries):
            try:
                retrieved = d[key]
                np.testing.assert_array_equal(retrieved, arr)
                break
            except KeyError:
                if retry == max_retries - 1:
                    raise
                time.sleep(0.01)  # Brief delay before retry

        # Also try to read arrays from other workers (if they exist)
        for other_worker in range(worker_id):
            try:
                other_key = f"worker_{other_worker}_array_{i}"
                if other_key in d:
                    other_arr = d[other_key]
                    assert isinstance(other_arr, np.ndarray)
            except KeyError:
                pass  # Other worker might not have created this array yet

    return True


def test_concurrent_numpy_access() -> None:
    """Test concurrent access to numpy arrays across multiple processes"""
    d = SharedDict(
        "concurrent_numpy", size=100 * 1024 * 1024, create=True, max_keys=128
    )

    num_workers = 3
    iterations = 10

    with mp.Pool(num_workers) as pool:
        # Start workers
        results = []
        for worker_id in range(num_workers):
            result = pool.apply_async(
                concurrent_numpy_worker, (worker_id, "concurrent_numpy", iterations)
            )
            results.append(result)

        # Wait for completion
        for result in results:
            assert result.get(timeout=30)

    # Verify all arrays are accessible
    total_arrays = 0
    for worker_id in range(num_workers):
        for i in range(iterations):
            key = f"worker_{worker_id}_array_{i}"
            arr = d[key]
            assert isinstance(arr, np.ndarray)
            assert arr.shape == (50, 50)
            total_arrays += 1

    assert total_arrays == num_workers * iterations
    d.close()
    d.unlink()
