"""
Real-world use case example: Shared cache system comparison.

This example demonstrates a practical use case where multiple worker processes
need to access and update a shared cache, comparing SharedDict vs Manager dict.

Run with: python examples/shared_cache_example.py
"""

import multiprocessing as mp
import random
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sharedbox import SharedDict


@dataclass
class CacheEntry:
    """Represents a cache entry with metadata."""

    value: Any
    created_at: datetime
    last_accessed: datetime
    hit_count: int = 0

    def access(self) -> None:
        """Mark this entry as accessed."""
        self.last_accessed = datetime.now()
        self.hit_count += 1

    def is_expired(self, ttl_seconds: int = 300) -> bool:
        """Check if entry is expired (default 5 minutes TTL)."""
        return (datetime.now() - self.created_at).total_seconds() > ttl_seconds


def cache_worker_shareddict(
    segment_name: str, worker_id: int, operations: int, cache_size: int
) -> tuple[int, dict[str, float]]:
    """Worker that performs cache operations using SharedDict."""
    start_time = time.perf_counter()

    stats = {
        "reads": 0,
        "writes": 0,
        "hits": 0,
        "misses": 0,
        "time_reading": 0.0,
        "time_writing": 0.0,
    }

    try:
        cache = SharedDict(segment_name, create=False)

        for i in range(operations):
            operation_type = random.choices(
                ["read", "write"],
                weights=[0.7, 0.3],  # 70% reads, 30% writes
            )[0]

            if operation_type == "read":
                # Read operation
                key = f"cache_key_{random.randint(0, cache_size - 1)}"

                read_start = time.perf_counter()
                if key in cache:
                    entry_data = cache[key]
                    # Simulate using the cached data
                    _ = entry_data
                    stats["hits"] += 1
                else:
                    stats["misses"] += 1
                stats["time_reading"] += time.perf_counter() - read_start
                stats["reads"] += 1

            else:
                # Write operation
                key = f"cache_key_{random.randint(0, cache_size - 1)}"

                # Create cache entry with some computational data
                computed_value = {
                    "result": sum(j**2 for j in range(50)),  # Simulate computation
                    "worker_id": worker_id,
                    "timestamp": time.time(),
                    "metadata": {"operation": i, "type": "computed"},
                }

                entry = CacheEntry(
                    value=computed_value,
                    created_at=datetime.now(),
                    last_accessed=datetime.now(),
                    hit_count=0,
                )

                write_start = time.perf_counter()
                cache[key] = entry
                stats["time_writing"] += time.perf_counter() - write_start
                stats["writes"] += 1

        cache.close()

    except Exception as e:
        print(f"SharedDict cache worker {worker_id} error: {e}")
        return worker_id, stats

    total_time = time.perf_counter() - start_time
    stats["total_time"] = total_time

    return worker_id, stats


def cache_worker_manager(
    manager_cache: dict[str, Any], worker_id: int, operations: int, cache_size: int
) -> tuple[int, dict[str, float]]:
    """Worker that performs cache operations using Manager dict."""
    start_time = time.perf_counter()

    stats = {
        "reads": 0,
        "writes": 0,
        "hits": 0,
        "misses": 0,
        "time_reading": 0.0,
        "time_writing": 0.0,
    }

    try:
        for i in range(operations):
            operation_type = random.choices(
                ["read", "write"],
                weights=[0.7, 0.3],  # 70% reads, 30% writes
            )[0]

            if operation_type == "read":
                # Read operation
                key = f"cache_key_{random.randint(0, cache_size - 1)}"

                read_start = time.perf_counter()
                if key in manager_cache:
                    entry_data = manager_cache[key]
                    # Simulate using the cached data
                    _ = entry_data
                    stats["hits"] += 1
                else:
                    stats["misses"] += 1
                stats["time_reading"] += time.perf_counter() - read_start
                stats["reads"] += 1

            else:
                # Write operation
                key = f"cache_key_{random.randint(0, cache_size - 1)}"

                # Create cache entry with some computational data
                computed_value = {
                    "result": sum(j**2 for j in range(50)),  # Simulate computation
                    "worker_id": worker_id,
                    "timestamp": time.time(),
                    "metadata": {"operation": i, "type": "computed"},
                }

                entry = CacheEntry(
                    value=computed_value,
                    created_at=datetime.now(),
                    last_accessed=datetime.now(),
                    hit_count=0,
                )

                write_start = time.perf_counter()
                manager_cache[key] = entry
                stats["time_writing"] += time.perf_counter() - write_start
                stats["writes"] += 1

    except Exception as e:
        print(f"Manager cache worker {worker_id} error: {e}")
        return worker_id, stats

    total_time = time.perf_counter() - start_time
    stats["total_time"] = total_time

    return worker_id, stats


def benchmark_cache_system(
    num_workers: int = 6, operations_per_worker: int = 1000, cache_size: int = 500
) -> dict[str, Any]:
    """Benchmark shared cache system performance."""
    print("\n=== Shared Cache Benchmark ===")
    print(f"Workers: {num_workers}")
    print(f"Operations per worker: {operations_per_worker}")
    print(f"Cache size: {cache_size} entries")

    results = {}

    # Test SharedDict cache
    print("\nTesting SharedDict cache system...")

    segment_name = "shared_cache_test"
    segment_size = 200 * 1024 * 1024  # 200MB

    cache = SharedDict(segment_name, size=segment_size, create=True)

    # Pre-populate cache with some entries
    for i in range(cache_size // 4):  # Fill 25% of cache initially
        key = f"cache_key_{i}"
        entry = CacheEntry(
            value={"initial_data": f"value_{i}", "size": i},
            created_at=datetime.now(),
            last_accessed=datetime.now(),
        )
        cache[key] = entry

    with mp.Pool(num_workers) as pool:
        worker_results = pool.starmap(
            cache_worker_shareddict,
            [
                (segment_name, worker_id, operations_per_worker, cache_size)
                for worker_id in range(num_workers)
            ],
        )

    cache.close()
    cache.unlink()

    # Aggregate SharedDict results
    shareddict_stats = aggregate_worker_stats(worker_results, "SharedDict")
    results["shareddict"] = shareddict_stats

    # Test Manager cache
    print("Testing Manager dict cache system...")

    with mp.Manager() as manager:
        manager_cache = manager.dict()

        # Pre-populate cache with some entries
        for i in range(cache_size // 4):  # Fill 25% of cache initially
            key = f"cache_key_{i}"
            entry = CacheEntry(
                value={"initial_data": f"value_{i}", "size": i},
                created_at=datetime.now(),
                last_accessed=datetime.now(),
            )
            manager_cache[key] = entry

        with mp.Pool(num_workers) as pool:
            worker_results = pool.starmap(
                cache_worker_manager,
                [
                    (manager_cache, worker_id, operations_per_worker, cache_size)
                    for worker_id in range(num_workers)
                ],
            )

    # Aggregate Manager results
    manager_stats = aggregate_worker_stats(worker_results, "Manager")
    results["manager"] = manager_stats

    return results


def aggregate_worker_stats(
    worker_results: list[tuple[int, dict[str, float]]], system_name: str
) -> dict[str, float]:
    """Aggregate statistics from all worker processes."""
    all_stats = [stats for _, stats in worker_results]

    aggregated = {
        "total_operations": sum(s["reads"] + s["writes"] for s in all_stats),
        "total_reads": sum(s["reads"] for s in all_stats),
        "total_writes": sum(s["writes"] for s in all_stats),
        "total_hits": sum(s["hits"] for s in all_stats),
        "total_misses": sum(s["misses"] for s in all_stats),
        "total_time": max(s["total_time"] for s in all_stats),  # Wall clock time
        "total_read_time": sum(s["time_reading"] for s in all_stats),
        "total_write_time": sum(s["time_writing"] for s in all_stats),
    }

    # Calculate derived metrics
    if aggregated["total_reads"] > 0:
        aggregated["hit_rate"] = aggregated["total_hits"] / aggregated["total_reads"]
        aggregated["avg_read_latency"] = (
            aggregated["total_read_time"] / aggregated["total_reads"] * 1000
        )  # ms
    else:
        aggregated["hit_rate"] = 0.0
        aggregated["avg_read_latency"] = 0.0

    if aggregated["total_writes"] > 0:
        aggregated["avg_write_latency"] = (
            aggregated["total_write_time"] / aggregated["total_writes"] * 1000
        )  # ms
    else:
        aggregated["avg_write_latency"] = 0.0

    aggregated["operations_per_sec"] = (
        aggregated["total_operations"] / aggregated["total_time"]
    )

    return aggregated


def data_consistency_test() -> dict[str, bool]:
    """Test data consistency between SharedDict and Manager dict."""
    print("\n=== Data Consistency Test ===")

    results = {}
    test_data = {
        "simple": {"key": "value", "number": 42},
        "complex": CacheEntry(
            value={"nested": {"deep": "structure"}},
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            hit_count=5,
        ),
        "list_data": [1, 2, {"nested": True}, "string"],
    }

    # Test SharedDict consistency
    segment_name = "consistency_test"
    cache = SharedDict(segment_name, size=50 * 1024 * 1024, create=True)

    try:
        for key, value in test_data.items():
            cache[key] = value
            retrieved = cache[key]
            results[f"shareddict_{key}"] = retrieved == value
    except Exception as e:
        print(f"SharedDict consistency test error: {e}")
        for key in test_data.keys():
            results[f"shareddict_{key}"] = False
    finally:
        cache.close()
        cache.unlink()

    # Test Manager dict consistency
    try:
        with mp.Manager() as manager:
            manager_dict = manager.dict()

            for key, value in test_data.items():
                manager_dict[key] = value
                retrieved = manager_dict[key]
                results[f"manager_{key}"] = retrieved == value
    except Exception as e:
        print(f"Manager dict consistency test error: {e}")
        for key in test_data.keys():
            results[f"manager_{key}"] = False

    return results


def run_cache_comparison() -> None:
    """Run comprehensive shared cache comparison."""
    print("Shared Cache System: SharedDict vs multiprocessing.Manager")
    print("=" * 60)

    # Set random seed for reproducible results
    random.seed(42)

    # Test different worker configurations
    test_configs = [
        (4, 800, 200),  # 4 workers, moderate ops, small cache
        (8, 1200, 400),  # 8 workers, more ops, medium cache
        (12, 1500, 600),  # 12 workers, heavy ops, large cache
    ]

    all_results = []

    for num_workers, ops_per_worker, cache_size in test_configs:
        print(
            f"\n{'-' * 20} Configuration: {num_workers} workers, {ops_per_worker} ops/worker, {cache_size} cache entries {'-' * 20}"
        )

        results = benchmark_cache_system(num_workers, ops_per_worker, cache_size)

        shareddict_stats = results["shareddict"]
        manager_stats = results["manager"]

        print("\nResults:")
        print("  SharedDict Cache:")
        print(f"    Operations/sec: {shareddict_stats['operations_per_sec']:.1f}")
        print(f"    Hit rate: {shareddict_stats['hit_rate']:.1%}")
        print(f"    Avg read latency: {shareddict_stats['avg_read_latency']:.2f}ms")
        print(f"    Avg write latency: {shareddict_stats['avg_write_latency']:.2f}ms")
        print(f"    Total time: {shareddict_stats['total_time']:.3f}s")

        print("  Manager dict Cache:")
        print(f"    Operations/sec: {manager_stats['operations_per_sec']:.1f}")
        print(f"    Hit rate: {manager_stats['hit_rate']:.1%}")
        print(f"    Avg read latency: {manager_stats['avg_read_latency']:.2f}ms")
        print(f"    Avg write latency: {manager_stats['avg_write_latency']:.2f}ms")
        print(f"    Total time: {manager_stats['total_time']:.3f}s")

        # Calculate improvements
        throughput_improvement = (
            shareddict_stats["operations_per_sec"] / manager_stats["operations_per_sec"]
        )
        read_latency_improvement = manager_stats["avg_read_latency"] / max(
            shareddict_stats["avg_read_latency"], 0.001
        )
        write_latency_improvement = manager_stats["avg_write_latency"] / max(
            shareddict_stats["avg_write_latency"], 0.001
        )

        print("  Performance Improvements (SharedDict vs Manager):")
        print(f"    Throughput: {throughput_improvement:.2f}x faster")
        print(f"    Read latency: {read_latency_improvement:.2f}x faster")
        print(f"    Write latency: {write_latency_improvement:.2f}x faster")

        all_results.append(
            {
                "config": (num_workers, ops_per_worker, cache_size),
                "throughput_improvement": throughput_improvement,
                "read_improvement": read_latency_improvement,
                "write_improvement": write_latency_improvement,
            }
        )

    # Data consistency test
    consistency_results = data_consistency_test()

    shareddict_consistent = all(
        v for k, v in consistency_results.items() if k.startswith("shareddict_")
    )
    manager_consistent = all(
        v for k, v in consistency_results.items() if k.startswith("manager_")
    )

    print("\nData Consistency Results:")
    print(
        f"  SharedDict: {'✓ All tests passed' if shareddict_consistent else '✗ Some tests failed'}"
    )
    print(
        f"  Manager dict: {'✓ All tests passed' if manager_consistent else '✗ Some tests failed'}"
    )

    # Summary
    avg_throughput_improvement = statistics.mean(
        r["throughput_improvement"] for r in all_results
    )
    avg_read_improvement = statistics.mean(r["read_improvement"] for r in all_results)
    avg_write_improvement = statistics.mean(r["write_improvement"] for r in all_results)

    print(f"\n{'=' * 60}")
    print("SHARED CACHE SUMMARY")
    print(f"{'=' * 60}")
    print("Average performance improvements (SharedDict vs Manager):")
    print(f"- Throughput: {avg_throughput_improvement:.2f}x faster")
    print(f"- Read latency: {avg_read_improvement:.2f}x faster")
    print(f"- Write latency: {avg_write_improvement:.2f}x faster")


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    run_cache_comparison()
