"""
Basic performance comparison between SharedDict and multiprocessing.Manager().dict().

This example demonstrates the fundamental performance differences between:
1. Our SharedDict - shared memory with lock striping
2. Multiprocessing Manager dict - proxy-based with centralized process

Run with: python examples/basic_comparison.py
"""

import multiprocessing as mp
import statistics
import time
from typing import Any

from sharedbox import SharedDict


def worker_shareddict_write(
    segment_name: str, worker_id: int, num_operations: int
) -> tuple[int, float]:
    """Worker function that writes to SharedDict and measures performance."""
    start_time = time.perf_counter()

    try:
        d = SharedDict(segment_name, create=False)

        for i in range(num_operations):
            key = f"worker_{worker_id}_item_{i}"
            value = {"worker_id": worker_id, "operation": i, "data": f"test_data_{i}"}
            d[key] = value

        # Mark completion
        d[f"worker_{worker_id}_done"] = True
        d.close()

    except Exception as e:
        print(f"SharedDict worker {worker_id} error: {e}")
        return worker_id, -1.0

    end_time = time.perf_counter()
    return worker_id, end_time - start_time


def worker_manager_write(
    manager_dict: dict[str, Any], worker_id: int, num_operations: int
) -> tuple[int, float]:
    """Worker function that writes to Manager dict and measures performance."""
    start_time = time.perf_counter()

    try:
        for i in range(num_operations):
            key = f"worker_{worker_id}_item_{i}"
            value = {"worker_id": worker_id, "operation": i, "data": f"test_data_{i}"}
            manager_dict[key] = value

        # Mark completion
        manager_dict[f"worker_{worker_id}_done"] = True

    except Exception as e:
        print(f"Manager worker {worker_id} error: {e}")
        return worker_id, -1.0

    end_time = time.perf_counter()
    return worker_id, end_time - start_time


def worker_shareddict_read(
    segment_name: str, worker_id: int, num_operations: int
) -> tuple[int, float]:
    """Worker function that reads from SharedDict and measures performance."""
    start_time = time.perf_counter()

    try:
        d = SharedDict(segment_name, create=False)

        # Read existing data
        for i in range(num_operations):
            key = f"data_item_{i}"
            if key in d:
                _ = d[key]  # Read operation

        d.close()

    except Exception as e:
        print(f"SharedDict reader {worker_id} error: {e}")
        return worker_id, -1.0

    end_time = time.perf_counter()
    return worker_id, end_time - start_time


def worker_manager_read(
    manager_dict: dict[str, Any], worker_id: int, num_operations: int
) -> tuple[int, float]:
    """Worker function that reads from Manager dict and measures performance."""
    start_time = time.perf_counter()

    try:
        # Read existing data
        for i in range(num_operations):
            key = f"data_item_{i}"
            if key in manager_dict:
                _ = manager_dict[key]  # Read operation

    except Exception as e:
        print(f"Manager reader {worker_id} error: {e}")
        return worker_id, -1.0

    end_time = time.perf_counter()
    return worker_id, end_time - start_time


def benchmark_write_operations(
    num_workers: int = 4, operations_per_worker: int = 1000
) -> dict[str, float]:
    """Benchmark write operations for both SharedDict and Manager dict."""
    print("\n=== Write Performance Test ===")
    print(f"Workers: {num_workers}, Operations per worker: {operations_per_worker}")

    results: dict[str, float] = {}

    # Test SharedDict
    print("\nTesting SharedDict writes...")
    segment_name = "benchmark_write_test"
    segment_size = 100 * 1024 * 1024  # 100MB

    d = SharedDict(segment_name, size=segment_size, create=True)

    start_time = time.perf_counter()

    with mp.Pool(num_workers) as pool:
        worker_results = pool.starmap(
            worker_shareddict_write,
            [
                (segment_name, worker_id, operations_per_worker)
                for worker_id in range(num_workers)
            ],
        )

    shareddict_time = time.perf_counter() - start_time

    # Verify all workers completed successfully
    failed_workers = [worker_id for worker_id, elapsed in worker_results if elapsed < 0]
    if failed_workers:
        print(f"SharedDict: Failed workers: {failed_workers}")

    d.close()
    d.unlink()
    results["shareddict_write"] = shareddict_time

    # Test Manager dict
    print("Testing Manager dict writes...")

    with mp.Manager() as manager:
        manager_dict = manager.dict()

        start_time = time.perf_counter()

        with mp.Pool(num_workers) as pool:
            worker_results = pool.starmap(
                worker_manager_write,
                [
                    (manager_dict, worker_id, operations_per_worker)
                    for worker_id in range(num_workers)
                ],
            )

        manager_time = time.perf_counter() - start_time

    # Verify all workers completed successfully
    failed_workers = [worker_id for worker_id, elapsed in worker_results if elapsed < 0]
    if failed_workers:
        print(f"Manager dict: Failed workers: {failed_workers}")

    results["manager_write"] = manager_time

    return results


def benchmark_read_operations(
    num_workers: int = 4, operations_per_worker: int = 1000
) -> dict[str, float]:
    """Benchmark read operations for both SharedDict and Manager dict."""
    print("\n=== Read Performance Test ===")
    print(f"Workers: {num_workers}, Operations per worker: {operations_per_worker}")

    results: dict[str, float] = {}

    # Prepare test data
    test_data = {
        f"data_item_{i}": f"test_value_{i}" for i in range(operations_per_worker)
    }

    # Test SharedDict reads
    print("\nTesting SharedDict reads...")
    segment_name = "benchmark_read_test"
    segment_size = 50 * 1024 * 1024  # 50MB

    d = SharedDict(segment_name, size=segment_size, create=True)

    # Populate with test data
    for key, value in test_data.items():
        d[key] = value

    start_time = time.perf_counter()

    with mp.Pool(num_workers) as pool:
        worker_results = pool.starmap(
            worker_shareddict_read,
            [
                (segment_name, worker_id, operations_per_worker)
                for worker_id in range(num_workers)
            ],
        )

    shareddict_time = time.perf_counter() - start_time
    d.close()
    d.unlink()
    results["shareddict_read"] = shareddict_time

    # Test Manager dict reads
    print("Testing Manager dict reads...")

    with mp.Manager() as manager:
        manager_dict = manager.dict()

        # Populate with test data
        manager_dict.update(test_data)

        start_time = time.perf_counter()

        with mp.Pool(num_workers) as pool:
            worker_results = pool.starmap(  # noqa
                worker_manager_read,
                [
                    (manager_dict, worker_id, operations_per_worker)
                    for worker_id in range(num_workers)
                ],
            )

        manager_time = time.perf_counter() - start_time

    results["manager_read"] = manager_time

    return results


def run_performance_comparison() -> None:
    """Run comprehensive performance comparison."""
    print("SharedDict vs multiprocessing.Manager Performance Comparison")
    print("=" * 60)

    test_configurations = [
        (2, 500),  # 2 workers, 500 ops each
        (4, 1000),  # 4 workers, 1000 ops each
        (8, 1500),  # 4 workers, 1500 ops each
    ]

    all_results: list[dict[str, Any]] = []

    for num_workers, ops_per_worker in test_configurations:
        print(
            f"\n{'=' * 20} Configuration: {num_workers} workers, {ops_per_worker} ops/worker {'=' * 20}"
        )

        # Run write benchmark
        write_results = benchmark_write_operations(num_workers, ops_per_worker)

        # Run read benchmark
        read_results = benchmark_read_operations(num_workers, ops_per_worker)

        # Combine results
        config_results = {
            "workers": num_workers,
            "operations_per_worker": ops_per_worker,
            "total_operations": num_workers * ops_per_worker,
            **write_results,
            **read_results,
        }

        all_results.append(config_results)

        # Calculate and display performance metrics
        write_speedup = (
            write_results["manager_write"] / write_results["shareddict_write"]
        )
        read_speedup = read_results["manager_read"] / read_results["shareddict_read"]

        total_ops = num_workers * ops_per_worker

        print(f"\nResults for {num_workers} workers × {ops_per_worker} operations:")
        print("  Write Operations:")
        print(
            f"    SharedDict:   {write_results['shareddict_write']:.3f}s ({total_ops / write_results['shareddict_write']:.1f} ops/sec)"
        )
        print(
            f"    Manager dict: {write_results['manager_write']:.3f}s ({total_ops / write_results['manager_write']:.1f} ops/sec)"
        )
        print(f"    Speedup:      {write_speedup:.2f}x faster")

        print("  Read Operations:")
        print(
            f"    SharedDict:   {read_results['shareddict_read']:.3f}s ({total_ops / read_results['shareddict_read']:.1f} ops/sec)"
        )
        print(
            f"    Manager dict: {read_results['manager_read']:.3f}s ({total_ops / read_results['manager_read']:.1f} ops/sec)"
        )
        print(f"    Speedup:      {read_speedup:.2f}x faster")

    # Summary statistics
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")

    write_speedups = [r["manager_write"] / r["shareddict_write"] for r in all_results]
    read_speedups = [r["manager_read"] / r["shareddict_read"] for r in all_results]

    print(
        f"Average write speedup: {statistics.mean(write_speedups):.2f}x (range: {min(write_speedups):.2f}x - {max(write_speedups):.2f}x)"
    )
    print(
        f"Average read speedup:  {statistics.mean(read_speedups):.2f}x (range: {min(read_speedups):.2f}x - {max(read_speedups):.2f}x)"
    )


if __name__ == "__main__":
    run_performance_comparison()
