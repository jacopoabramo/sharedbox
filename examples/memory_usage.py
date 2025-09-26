"""
Memory usage and data structure comparison between SharedDict and multiprocessing.Manager().dict().

This example demonstrates memory efficiency and data structure handling differences.

Run with: python examples/memory_usage.py
"""

import multiprocessing as mp
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, NamedTuple, Tuple

try:
    import psutil
except ImportError as e:
    msg = "psutil is required for memory usage analysis. Install with `pip install psutil`."
    raise ImportError(msg) from e

from sharedbox import SharedDict


class MemoryStats(NamedTuple):
    """Memory usage statistics."""

    rss: int  # Resident Set Size in bytes
    vms: int  # Virtual Memory Size in bytes
    percent: float  # Percentage of total system memory
    shared: int  # Shared memory in bytes


@dataclass
class TestData:
    """Complex test data structure."""

    id: int
    name: str
    values: list[float]
    metadata: dict[str, Any]
    timestamp: float


def get_memory_usage() -> MemoryStats:
    """Get current process memory usage."""
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    memory_percent = process.memory_percent()

    # Try to get shared memory (not available on all platforms)
    try:
        shared = memory_info.shared
    except AttributeError:
        shared = 0

    return MemoryStats(
        rss=memory_info.rss, vms=memory_info.vms, percent=memory_percent, shared=shared
    )


def create_test_data(num_records: int) -> dict[str, TestData]:
    """Create test data for memory usage comparison."""
    data = {}

    for i in range(num_records):
        test_record = TestData(
            id=i,
            name=f"record_{i:05d}",
            values=[float(j) for j in range(10)],  # 10 float values
            metadata={
                "category": f"cat_{i % 10}",
                "priority": i % 5,
                "tags": [f"tag_{j}" for j in range(i % 3 + 1)],
                "nested": {
                    "level1": {"level2": f"deep_value_{i}"},
                    "counters": list(range(i % 5)),
                },
            },
            timestamp=time.time() + i,
        )
        data[f"record_{i}"] = test_record

    return data


def memory_worker_shareddict(
    segment_name: str, worker_id: int, num_records: int
) -> Tuple[int, MemoryStats, MemoryStats]:
    """Worker that uses SharedDict and measures memory usage."""
    initial_memory = get_memory_usage()

    try:
        d = SharedDict(segment_name, create=False)

        # Create and store test data
        test_data = create_test_data(num_records)

        for key, value in test_data.items():
            d[f"worker_{worker_id}_{key}"] = value

        # Measure memory after operations
        peak_memory = get_memory_usage()

        d.close()

        return worker_id, initial_memory, peak_memory

    except Exception as e:
        print(f"SharedDict memory worker {worker_id} error: {e}")
        return worker_id, initial_memory, initial_memory


def memory_worker_manager(
    manager_dict: dict[str, Any], worker_id: int, num_records: int
) -> Tuple[int, MemoryStats, MemoryStats]:
    """Worker that uses Manager dict and measures memory usage."""
    initial_memory = get_memory_usage()

    try:
        # Create and store test data
        test_data = create_test_data(num_records)

        for key, value in test_data.items():
            manager_dict[f"worker_{worker_id}_{key}"] = value

        # Measure memory after operations
        peak_memory = get_memory_usage()

        return worker_id, initial_memory, peak_memory

    except Exception as e:
        print(f"Manager memory worker {worker_id} error: {e}")
        return worker_id, initial_memory, initial_memory


def benchmark_memory_usage(
    num_workers: int = 4, records_per_worker: int = 1000
) -> Dict[str, Any]:
    """Benchmark memory usage for both SharedDict and Manager dict."""
    print("\n=== Memory Usage Benchmark ===")
    print(f"Workers: {num_workers}, Records per worker: {records_per_worker}")

    results: Dict[str, Any] = {}

    # Baseline memory measurement
    baseline_memory = get_memory_usage()
    print(f"Baseline memory: {baseline_memory.rss / 1024 / 1024:.1f} MB RSS")

    # Test SharedDict memory usage
    print("\nTesting SharedDict memory usage...")
    segment_name = "memory_test_shareddict"
    segment_size = 500 * 1024 * 1024  # 500MB

    d = SharedDict(segment_name, size=segment_size, create=True)

    with mp.Pool(num_workers) as pool:
        worker_results = pool.starmap(
            memory_worker_shareddict,
            [
                (segment_name, worker_id, records_per_worker)
                for worker_id in range(num_workers)
            ],
        )

    d.close()
    d.unlink()

    # Calculate SharedDict memory stats
    initial_memories = [result[1] for result in worker_results]
    peak_memories = [result[2] for result in worker_results]

    avg_initial_rss = sum(mem.rss for mem in initial_memories) / len(initial_memories)
    avg_peak_rss = sum(mem.rss for mem in peak_memories) / len(peak_memories)
    avg_memory_increase = avg_peak_rss - avg_initial_rss

    results["shareddict"] = {
        "avg_initial_rss_mb": avg_initial_rss / 1024 / 1024,
        "avg_peak_rss_mb": avg_peak_rss / 1024 / 1024,
        "avg_memory_increase_mb": avg_memory_increase / 1024 / 1024,
        "total_records": num_workers * records_per_worker,
    }

    # Test Manager dict memory usage
    print("Testing Manager dict memory usage...")

    with mp.Manager() as manager:
        manager_dict = manager.dict()

        with mp.Pool(num_workers) as pool:
            worker_results = pool.starmap(
                memory_worker_manager,
                [
                    (manager_dict, worker_id, records_per_worker)
                    for worker_id in range(num_workers)
                ],
            )

    # Calculate Manager dict memory stats
    initial_memories = [result[1] for result in worker_results]
    peak_memories = [result[2] for result in worker_results]

    avg_initial_rss = sum(mem.rss for mem in initial_memories) / len(initial_memories)
    avg_peak_rss = sum(mem.rss for mem in peak_memories) / len(peak_memories)
    avg_memory_increase = avg_peak_rss - avg_initial_rss

    results["manager"] = {
        "avg_initial_rss_mb": avg_initial_rss / 1024 / 1024,
        "avg_peak_rss_mb": avg_peak_rss / 1024 / 1024,
        "avg_memory_increase_mb": avg_memory_increase / 1024 / 1024,
        "total_records": num_workers * records_per_worker,
    }

    return results


def serialization_benchmark() -> Dict[str, float]:
    """Benchmark serialization overhead differences."""
    print("\n=== Serialization Overhead Test ===")

    # Create complex nested data
    complex_data = {
        "users": [
            TestData(i, f"user_{i}", [1.1, 2.2, 3.3], {"role": "admin"}, time.time())
            for i in range(100)
        ],
        "counters": {f"category_{i}": Counter(f"test_string_{i}") for i in range(10)},
        "nested_dict": {
            "level1": {
                "level2": {
                    "level3": [{"deep_key": f"deep_value_{i}"} for i in range(20)]
                }
            }
        },
        "defaultdicts": [
            defaultdict(list, {f"key_{i}": [i, i * 2, i * 3]}) for i in range(10)
        ],
    }

    results: Dict[str, float] = {}

    # Test SharedDict storage (minimal serialization)
    segment_name = "serialization_test"
    d = SharedDict(segment_name, size=100 * 1024 * 1024, create=True)

    start_time = time.perf_counter()
    for key, value in complex_data.items():
        d[key] = value
    shareddict_store_time = time.perf_counter() - start_time

    start_time = time.perf_counter()
    for key in complex_data.keys():
        _ = d[key]
    shareddict_retrieve_time = time.perf_counter() - start_time

    d.close()
    d.unlink()

    results["shareddict_store"] = shareddict_store_time
    results["shareddict_retrieve"] = shareddict_retrieve_time

    # Test Manager dict (full serialization)
    with mp.Manager() as manager:
        manager_dict = manager.dict()

        start_time = time.perf_counter()
        for key, value in complex_data.items():
            manager_dict[key] = value
        manager_store_time = time.perf_counter() - start_time

        start_time = time.perf_counter()
        for key in complex_data.keys():
            _ = manager_dict[key]
        manager_retrieve_time = time.perf_counter() - start_time

    results["manager_store"] = manager_store_time
    results["manager_retrieve"] = manager_retrieve_time

    return results


def data_structure_compatibility_test() -> Dict[str, bool]:
    """Test compatibility with various Python data structures."""
    print("\n=== Data Structure Compatibility Test ===")

    test_cases = {
        "dataclass": TestData(1, "test", [1.0, 2.0], {"key": "value"}, time.time()),
        "counter": Counter("hello world"),
        "defaultdict": defaultdict(list, {"items": [1, 2, 3]}),
        "nested_dict": {"level1": {"level2": {"level3": "deep_value"}}},
        "list_of_dicts": [{"id": i, "value": f"item_{i}"} for i in range(5)],
        "tuple_complex": (1, "string", [1, 2, 3], {"nested": True}),
        "set_data": {1, 2, 3, 4, 5},
        "frozenset_data": frozenset([1, 2, 3, 4, 5]),
    }

    results: Dict[str, bool] = {}

    # Test SharedDict
    segment_name = "compatibility_test"
    d = SharedDict(segment_name, size=50 * 1024 * 1024, create=True)

    for test_name, test_value in test_cases.items():
        try:
            d[test_name] = test_value
            retrieved_value = d[test_name]

            # Verify data integrity
            if test_name == "set_data" or test_name == "frozenset_data":
                # Sets need special comparison
                success = retrieved_value == test_value and (
                    type(retrieved_value) is type(test_value)
                )
            else:
                success = retrieved_value == test_value

            results[f"shareddict_{test_name}"] = success

        except Exception as e:
            print(f"SharedDict failed for {test_name}: {e}")
            results[f"shareddict_{test_name}"] = False

    d.close()
    d.unlink()

    # Test Manager dict
    with mp.Manager() as manager:
        manager_dict = manager.dict()

        for test_name, test_value in test_cases.items():
            try:
                manager_dict[test_name] = test_value
                retrieved_value = manager_dict[test_name]

                # Verify data integrity
                if test_name == "set_data" or test_name == "frozenset_data":
                    success = retrieved_value == test_value and (
                        type(retrieved_value) is type(test_value)
                    )
                else:
                    success = retrieved_value == test_value

                results[f"manager_{test_name}"] = success

            except Exception as e:
                print(f"Manager dict failed for {test_name}: {e}")
                results[f"manager_{test_name}"] = False

    return results


def run_memory_analysis() -> None:
    """Run comprehensive memory and efficiency analysis."""
    print("Memory Usage and Efficiency Analysis: SharedDict vs multiprocessing.Manager")
    print("=" * 80)

    # Memory usage tests
    memory_configs = [
        (2, 500),  # Light load
        (4, 1000),  # Medium load
        (6, 1500),  # Heavy load
    ]

    for num_workers, records in memory_configs:
        memory_results = benchmark_memory_usage(num_workers, records)

        shareddict_mem = memory_results["shareddict"]
        manager_mem = memory_results["manager"]

        print(f"\n{num_workers} workers × {records} records:")
        print("  SharedDict:")
        print(f"    Avg peak memory: {shareddict_mem['avg_peak_rss_mb']:.1f} MB")
        print(f"    Memory increase: {shareddict_mem['avg_memory_increase_mb']:.1f} MB")
        print("  Manager dict:")
        print(f"    Avg peak memory: {manager_mem['avg_peak_rss_mb']:.1f} MB")
        print(f"    Memory increase: {manager_mem['avg_memory_increase_mb']:.1f} MB")

        efficiency = manager_mem["avg_memory_increase_mb"] / max(
            shareddict_mem["avg_memory_increase_mb"], 0.1
        )
        print(
            f"  Memory efficiency: SharedDict uses {efficiency:.1f}x less memory increase"
        )

    # Serialization overhead test
    serialization_results = serialization_benchmark()

    store_speedup = (
        serialization_results["manager_store"]
        / serialization_results["shareddict_store"]
    )
    retrieve_speedup = (
        serialization_results["manager_retrieve"]
        / serialization_results["shareddict_retrieve"]
    )

    print("\nSerialization Performance:")
    print("  Store operations:")
    print(f"    SharedDict: {serialization_results['shareddict_store']:.4f}s")
    print(f"    Manager dict: {serialization_results['manager_store']:.4f}s")
    print(f"    Speedup: {store_speedup:.2f}x faster")

    print("  Retrieve operations:")
    print(f"    SharedDict: {serialization_results['shareddict_retrieve']:.4f}s")
    print(f"    Manager dict: {serialization_results['manager_retrieve']:.4f}s")
    print(f"    Speedup: {retrieve_speedup:.2f}x faster")

    # Data structure compatibility test
    compatibility_results = data_structure_compatibility_test()

    shareddict_success = sum(
        1 for k, v in compatibility_results.items() if k.startswith("shareddict_") and v
    )
    manager_success = sum(
        1 for k, v in compatibility_results.items() if k.startswith("manager_") and v
    )
    total_tests = len(
        [k for k in compatibility_results.keys() if k.startswith("shareddict_")]
    )

    print("\nData Structure Compatibility:")
    print(f"  SharedDict: {shareddict_success}/{total_tests} data structures supported")
    print(f"  Manager dict: {manager_success}/{total_tests} data structures supported")

    # Show any compatibility issues
    for test_name in ["dataclass", "counter", "defaultdict", "nested_dict", "set_data"]:
        shareddict_ok = compatibility_results.get(f"shareddict_{test_name}", False)
        manager_ok = compatibility_results.get(f"manager_{test_name}", False)

        if not shareddict_ok or not manager_ok:
            print(
                f"  Issue with {test_name}: SharedDict={shareddict_ok}, Manager={manager_ok}"
            )


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    run_memory_analysis()
