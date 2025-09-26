"""
Concurrent access patterns comparison between SharedDict and multiprocessing.Manager().dict().

This example demonstrates different concurrent access patterns and shows how
SharedDict's lock striping provides better performance for concurrent operations.

Run with: python examples/concurrent_access.py
"""

import multiprocessing as mp
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

from sharedbox import SharedDict


def mixed_operations_shareddict(
    segment_name: str, worker_id: int, num_operations: int, read_ratio: float = 0.7
) -> tuple[int, float, int, int]:
    """Perform mixed read/write operations on SharedDict."""
    start_time = time.perf_counter()
    reads = 0
    writes = 0

    try:
        d = SharedDict(segment_name, create=False)

        for i in range(num_operations):
            # Randomly choose read or write based on ratio
            if random.random() < read_ratio:
                # Read operation
                key = f"shared_data_{random.randint(0, 999)}"
                if key in d:
                    _ = d[key]
                    reads += 1
            else:
                # Write operation
                key = f"worker_{worker_id}_data_{i}"
                value = {"timestamp": time.time(), "worker": worker_id, "op": i}
                d[key] = value
                writes += 1

        d.close()

    except Exception as e:
        print(f"SharedDict worker {worker_id} error: {e}")
        return worker_id, -1.0, 0, 0

    end_time = time.perf_counter()
    return worker_id, end_time - start_time, reads, writes


def mixed_operations_manager(
    manager_dict: dict[str, Any],
    worker_id: int,
    num_operations: int,
    read_ratio: float = 0.7,
) -> tuple[int, float, int, int]:
    """Perform mixed read/write operations on Manager dict."""
    start_time = time.perf_counter()
    reads = 0
    writes = 0

    try:
        for i in range(num_operations):
            # Randomly choose read or write based on ratio
            if random.random() < read_ratio:
                # Read operation
                key = f"shared_data_{random.randint(0, 999)}"
                if key in manager_dict:
                    _ = manager_dict[key]
                    reads += 1
            else:
                # Write operation
                key = f"worker_{worker_id}_data_{i}"
                value = {"timestamp": time.time(), "worker": worker_id, "op": i}
                manager_dict[key] = value
                writes += 1

    except Exception as e:
        print(f"Manager worker {worker_id} error: {e}")
        return worker_id, -1.0, 0, 0

    end_time = time.perf_counter()
    return worker_id, end_time - start_time, reads, writes


def heavy_computation_shareddict(
    segment_name: str, worker_id: int, num_computations: int
) -> tuple[int, float]:
    """Perform heavy computation with periodic SharedDict updates."""
    start_time = time.perf_counter()

    try:
        d = SharedDict(segment_name, create=False)

        results = []
        for i in range(num_computations):
            # Simulate heavy computation
            result = sum(j**2 for j in range(1000))
            results.append(result)

            # Periodic update to shared dict
            if i % 100 == 0:
                d[f"worker_{worker_id}_checkpoint_{i}"] = {
                    "progress": i / num_computations,
                    "partial_results": len(results),
                    "timestamp": time.time(),
                }

        # Final result
        d[f"worker_{worker_id}_final"] = {
            "total_results": len(results),
            "sum": sum(results),
            "completion_time": time.time(),
        }

        d.close()

    except Exception as e:
        print(f"Heavy computation worker {worker_id} error: {e}")
        return worker_id, -1.0

    end_time = time.perf_counter()
    return worker_id, end_time - start_time


def heavy_computation_manager(
    manager_dict: dict[str, Any], worker_id: int, num_computations: int
) -> tuple[int, float]:
    """Perform heavy computation with periodic Manager dict updates."""
    start_time = time.perf_counter()

    try:
        results = []
        for i in range(num_computations):
            # Simulate heavy computation
            result = sum(j**2 for j in range(1000))
            results.append(result)

            # Periodic update to manager dict
            if i % 100 == 0:
                manager_dict[f"worker_{worker_id}_checkpoint_{i}"] = {
                    "progress": i / num_computations,
                    "partial_results": len(results),
                    "timestamp": time.time(),
                }

        # Final result
        manager_dict[f"worker_{worker_id}_final"] = {
            "total_results": len(results),
            "sum": sum(results),
            "completion_time": time.time(),
        }

    except Exception as e:
        print(f"Heavy computation manager worker {worker_id} error: {e}")
        return worker_id, -1.0

    end_time = time.perf_counter()
    return worker_id, end_time - start_time


def benchmark_mixed_operations(
    num_workers: int = 6, operations_per_worker: int = 2000
) -> dict[str, Any]:
    """Benchmark mixed read/write operations."""
    print("\n=== Mixed Operations Test ===")
    print(f"Workers: {num_workers}, Operations per worker: {operations_per_worker}")
    print("Pattern: 70% reads, 30% writes")

    results: dict[str, Any] = {}

    # Test SharedDict
    print("\nTesting SharedDict mixed operations...")
    segment_name = "benchmark_mixed_test"
    segment_size = 200 * 1024 * 1024  # 200MB

    d = SharedDict(segment_name, size=segment_size, create=True)

    # Pre-populate with some data for reads
    for i in range(1000):
        d[f"shared_data_{i}"] = f"pre_populated_value_{i}"

    start_time = time.perf_counter()

    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = [
            executor.submit(
                mixed_operations_shareddict,
                segment_name,
                worker_id,
                operations_per_worker,
            )
            for worker_id in range(num_workers)
        ]

        worker_results = [future.result() for future in as_completed(futures)]

    shareddict_time = time.perf_counter() - start_time

    total_reads = sum(reads for _, _, reads, _ in worker_results if reads >= 0)
    total_writes = sum(writes for _, _, _, writes in worker_results if writes >= 0)

    d.unlink()
    results["shareddict"] = {
        "total_time": shareddict_time,
        "total_reads": total_reads,
        "total_writes": total_writes,
        "read_ops_per_sec": total_reads / shareddict_time,
        "write_ops_per_sec": total_writes / shareddict_time,
    }

    # Test Manager dict
    print("Testing Manager dict mixed operations...")

    with mp.Manager() as manager:
        manager_dict = manager.dict()

        # Pre-populate with some data for reads
        for i in range(1000):
            manager_dict[f"shared_data_{i}"] = f"pre_populated_value_{i}"

        start_time = time.perf_counter()

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = [
                executor.submit(
                    mixed_operations_manager,
                    manager_dict,
                    worker_id,
                    operations_per_worker,
                )
                for worker_id in range(num_workers)
            ]

            worker_results = [future.result() for future in as_completed(futures)]

        manager_time = time.perf_counter() - start_time

    total_reads = sum(reads for _, _, reads, _ in worker_results if reads >= 0)
    total_writes = sum(writes for _, _, _, writes in worker_results if writes >= 0)

    results["manager"] = {
        "total_time": manager_time,
        "total_reads": total_reads,
        "total_writes": total_writes,
        "read_ops_per_sec": total_reads / manager_time,
        "write_ops_per_sec": total_writes / manager_time,
    }

    return results


def benchmark_heavy_computation(
    num_workers: int = 4, computations_per_worker: int = 1000
) -> dict[str, Any]:
    """Benchmark performance when combining computation with shared data updates."""
    print("\n=== Heavy Computation + Shared Updates Test ===")
    print(f"Workers: {num_workers}, Computations per worker: {computations_per_worker}")

    results: dict[str, Any] = {}

    # Test SharedDict
    print("\nTesting SharedDict with heavy computation...")
    segment_name = "benchmark_heavy_test"
    segment_size = 100 * 1024 * 1024  # 100MB

    d = SharedDict(segment_name, size=segment_size, create=True)

    start_time = time.perf_counter()

    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = [
            executor.submit(
                heavy_computation_shareddict,
                segment_name,
                worker_id,
                computations_per_worker,
            )
            for worker_id in range(num_workers)
        ]

        worker_results = [future.result() for future in as_completed(futures)]

    shareddict_time = time.perf_counter() - start_time

    # Verify all workers completed
    completed_workers = len([result for result in worker_results if result[1] >= 0])

    d.unlink()
    results["shareddict"] = {
        "total_time": shareddict_time,
        "completed_workers": completed_workers,
        "avg_time_per_worker": shareddict_time / max(1, completed_workers),
    }

    # Test Manager dict
    print("Testing Manager dict with heavy computation...")

    with mp.Manager() as manager:
        manager_dict = manager.dict()

        start_time = time.perf_counter()

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = [
                executor.submit(
                    heavy_computation_manager,
                    manager_dict,
                    worker_id,
                    computations_per_worker,
                )
                for worker_id in range(num_workers)
            ]

            worker_results = [future.result() for future in as_completed(futures)]

        manager_time = time.perf_counter() - start_time

    # Verify all workers completed
    completed_workers = len([result for result in worker_results if result[1] >= 0])

    results["manager"] = {
        "total_time": manager_time,
        "completed_workers": completed_workers,
        "avg_time_per_worker": manager_time / max(1, completed_workers),
    }

    return results


def run_concurrent_benchmarks() -> None:
    """Run comprehensive concurrent access benchmarks."""
    print("Concurrent Access Patterns: SharedDict vs multiprocessing.Manager")
    print("=" * 70)

    # Test different concurrency levels
    concurrency_tests = [
        (4, 1000),  # 4 workers, moderate load
        (8, 1500),  # 8 workers, higher load
        (12, 2000),  # 12 workers, high load
    ]

    print("\nMixed Operations Benchmark (70% reads, 30% writes)")
    print("-" * 50)

    for num_workers, ops_per_worker in concurrency_tests:
        mixed_results = benchmark_mixed_operations(num_workers, ops_per_worker)

        shareddict_results = mixed_results["shareddict"]
        manager_results = mixed_results["manager"]

        print(f"\n{num_workers} workers × {ops_per_worker} operations:")
        print("  SharedDict:")
        print(f"    Total time: {shareddict_results['total_time']:.3f}s")
        print(f"    Read ops/sec: {shareddict_results['read_ops_per_sec']:.1f}")
        print(f"    Write ops/sec: {shareddict_results['write_ops_per_sec']:.1f}")

        print("  Manager dict:")
        print(f"    Total time: {manager_results['total_time']:.3f}s")
        print(f"    Read ops/sec: {manager_results['read_ops_per_sec']:.1f}")
        print(f"    Write ops/sec: {manager_results['write_ops_per_sec']:.1f}")

        read_speedup = (
            manager_results["read_ops_per_sec"] / shareddict_results["read_ops_per_sec"]
        )
        write_speedup = (
            manager_results["write_ops_per_sec"]
            / shareddict_results["write_ops_per_sec"]
        )

        print("  SharedDict vs Manager:")
        print(f"    Read speedup: {1 / read_speedup:.2f}x faster")
        print(f"    Write speedup: {1 / write_speedup:.2f}x faster")

    print(f"\n{'-' * 50}")
    print("Heavy Computation + Shared Updates Benchmark")
    print("-" * 50)

    computation_tests = [
        (2, 500),  # 2 workers, moderate computation
        (4, 800),  # 4 workers, higher computation
        (6, 1000),  # 6 workers, heavy computation
    ]

    for num_workers, computations in computation_tests:
        heavy_results = benchmark_heavy_computation(num_workers, computations)

        shareddict_results = heavy_results["shareddict"]
        manager_results = heavy_results["manager"]

        print(f"\n{num_workers} workers × {computations} computations:")
        print(f"  SharedDict: {shareddict_results['total_time']:.3f}s")
        print(f"  Manager dict: {manager_results['total_time']:.3f}s")

        speedup = manager_results["total_time"] / shareddict_results["total_time"]
        print(f"  Speedup: {speedup:.2f}x faster")

    print(f"\n{'=' * 70}")
    print("KEY INSIGHTS")
    print(f"{'=' * 70}")
    print("1. SharedDict excels in high-concurrency scenarios due to lock striping")
    print("2. Manager dict has proxy overhead that increases with operation count")
    print("3. SharedDict's direct memory access eliminates serialization bottlenecks")
    print("4. Performance gap widens as concurrency and operation frequency increase")


if __name__ == "__main__":
    # Set random seed for reproducible results
    random.seed(42)
    mp.set_start_method("spawn", force=True)
    run_concurrent_benchmarks()
