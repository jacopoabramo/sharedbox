#!/usr/bin/env python3
"""
Mixed Data Types Performance Benchmark: SharedDict vs multiprocessing.Manager().dict()

This benchmark compares the performance of storing and retrieving mixed data types
(numpy arrays combined with built-in Python types) between SharedDict and
multiprocessing.Manager().dict().
"""

import multiprocessing as mp
import time
from typing import Any

import numpy as np

from sharedbox import SharedDict


def create_mixed_test_data(data_id: int, numpy_ratio: float = 0.5) -> dict[str, Any]:
    """Create test data with mixed numpy arrays and built-in Python types"""
    data = {}

    # Calculate how many of each type to create
    total_items = 20
    numpy_items = int(total_items * numpy_ratio)
    builtin_items = total_items - numpy_items

    # Create numpy arrays
    for i in range(numpy_items):
        key = f"numpy_{data_id}_{i}"
        # Vary array sizes and types
        if i % 3 == 0:
            data[key] = np.random.rand(50, 50).astype(np.float32)
        elif i % 3 == 1:
            data[key] = np.random.randint(0, 1000, (30, 30), dtype=np.int32)
        else:
            data[key] = np.random.rand(100).astype(np.float64)

    # Create built-in Python objects
    for i in range(builtin_items):
        key = f"builtin_{data_id}_{i}"
        # Vary built-in types
        if i % 4 == 0:
            data[key] = {
                "id": data_id * 100 + i,
                "values": [j * 1.5 for j in range(20)],
                "metadata": {"type": "float_list", "count": 20},
            }
        elif i % 4 == 1:
            data[key] = f"test_string_{data_id}_{i}_" + "x" * 50
        elif i % 4 == 2:
            data[key] = list(range(data_id * 10, data_id * 10 + 50))
        else:
            data[key] = {
                "nested": {
                    "deep": {
                        "data": [data_id, i, time.time()],
                        "flags": {"active": True, "processed": False},
                    }
                }
            }

    return data


def mp_dict_mixed_worker(
    shared_dict: Any,
    worker_id: int,
    iterations: int,
    numpy_ratio: float,
    results_queue: Any,
) -> None:
    """Worker function for multiprocessing.Manager().dict() mixed data test"""
    print(
        f"  MP Dict Worker {worker_id}: Starting {iterations} mixed operations (numpy ratio: {numpy_ratio:.1f})..."
    )

    times: list[tuple[float, float]] = []

    for i in range(iterations):
        # Create mixed test data
        test_data = create_mixed_test_data(worker_id * 1000 + i, numpy_ratio)

        # Convert numpy arrays to lists for pickle serialization
        serialized_data = {}
        for key, value in test_data.items():
            if isinstance(value, np.ndarray):
                serialized_data[key] = {
                    "__numpy_array__": True,
                    "data": value.tolist(),
                    "dtype": str(value.dtype),
                    "shape": value.shape,
                }
            else:
                serialized_data[key] = value

        # Time store operations
        start = time.perf_counter()
        for key, value in serialized_data.items():
            shared_dict[f"worker_{worker_id}_{key}_{i}"] = value
        store_time = time.perf_counter() - start

        # Time retrieve operations
        start = time.perf_counter()
        for key in serialized_data.keys():
            _ = shared_dict[f"worker_{worker_id}_{key}_{i}"]
        retrieve_time = time.perf_counter() - start

        times.append((store_time, retrieve_time))

    print(f"  MP Dict Worker {worker_id}: Completed")
    results_queue.put((worker_id, times))


def shareddict_mixed_worker(
    worker_id: int, iterations: int, numpy_ratio: float, results_queue: Any
) -> None:
    """Worker function for SharedDict mixed data test"""
    print(
        f"  SharedDict Worker {worker_id}: Starting {iterations} mixed operations (numpy ratio: {numpy_ratio:.1f})..."
    )

    d = SharedDict("mixed_benchmark", create=False)
    times: list[tuple[float, float]] = []

    for i in range(iterations):
        # Create mixed test data (numpy arrays stay as numpy arrays)
        test_data = create_mixed_test_data(worker_id * 1000 + i, numpy_ratio)

        # Time store operations
        start = time.perf_counter()
        for key, value in test_data.items():
            d[f"worker_{worker_id}_{key}_{i}"] = value
        store_time = time.perf_counter() - start

        # Time retrieve operations
        start = time.perf_counter()
        for key in test_data.keys():
            _ = d[f"worker_{worker_id}_{key}_{i}"]
        retrieve_time = time.perf_counter() - start

        times.append((store_time, retrieve_time))

    print(f"  SharedDict Worker {worker_id}: Completed")
    results_queue.put((worker_id, times))


def benchmark_mixed_data_performance() -> None:
    """Run comprehensive mixed data types performance benchmark"""
    print("=" * 80)
    print("MIXED DATA TYPES PERFORMANCE BENCHMARK")
    print("SharedDict vs multiprocessing.Manager().dict()")
    print("Testing various ratios of numpy arrays to built-in Python types")
    print("=" * 80)

    # Test configurations with different numpy/built-in ratios
    test_configs: list[dict[str, Any]] = [
        {
            "name": "Mostly Built-in (20% numpy)",
            "numpy_ratio": 0.2,
            "workers": 2,
            "iterations": 5,
        },
        {
            "name": "Equal Mix (50% numpy)",
            "numpy_ratio": 0.5,
            "workers": 2,
            "iterations": 5,
        },
        {
            "name": "Mostly Numpy (80% numpy)",
            "numpy_ratio": 0.8,
            "workers": 2,
            "iterations": 5,
        },
        {
            "name": "All Numpy (100% numpy)",
            "numpy_ratio": 1.0,
            "workers": 2,
            "iterations": 3,
        },
        {
            "name": "All Built-in (0% numpy)",
            "numpy_ratio": 0.0,
            "workers": 2,
            "iterations": 8,
        },
    ]

    results_summary: list[dict[str, Any]] = []

    for config in test_configs:
        print(f"\n{config['name']} Test:")
        print(f"  Numpy Ratio: {config['numpy_ratio']:.1f}")
        print(
            f"  Workers: {config['workers']}, Iterations per worker: {config['iterations']}"
        )
        print("  ~20 objects per iteration (mixed types)")

        # Test multiprocessing.Manager().dict()
        print("\n📦 Testing multiprocessing.Manager().dict() (pickle serialization):")
        manager = mp.Manager()
        mp_dict = manager.dict()
        mp_queue = manager.Queue()

        mp_start = time.perf_counter()

        mp_processes = []
        for worker_id in range(config["workers"]):
            p = mp.Process(
                target=mp_dict_mixed_worker,
                args=(
                    mp_dict,
                    worker_id,
                    config["iterations"],
                    config["numpy_ratio"],
                    mp_queue,
                ),
            )
            p.start()
            mp_processes.append(p)

        for p in mp_processes:
            p.join(timeout=90)

        mp_end = time.perf_counter()
        mp_total_time = mp_end - mp_start

        # Collect MP results
        mp_times: list[tuple[float, float]] = []
        while not mp_queue.empty():
            worker_id, times = mp_queue.get()
            mp_times.extend(times)

        # Test SharedDict
        print("\n🚀 Testing SharedDict (native numpy + pickle for built-ins):")

        # Calculate memory size needed
        memory_mb = 200  # Conservative estimate for mixed data

        d = SharedDict(
            "mixed_benchmark", size=memory_mb * 1024 * 1024, create=True, max_keys=512
        )
        sd_queue = manager.Queue()

        sd_start = time.perf_counter()

        sd_processes = []
        for worker_id in range(config["workers"]):
            p = mp.Process(
                target=shareddict_mixed_worker,
                args=(worker_id, config["iterations"], config["numpy_ratio"], sd_queue),
            )
            p.start()
            sd_processes.append(p)

        for p in sd_processes:
            p.join(timeout=90)

        sd_end = time.perf_counter()
        sd_total_time = sd_end - sd_start

        # Collect SharedDict results
        sd_times: list[tuple[float, float]] = []
        while not sd_queue.empty():
            worker_id, times = sd_queue.get()
            sd_times.extend(times)

        # Calculate and display results
        if mp_times and sd_times:
            mp_avg_store = sum(t[0] for t in mp_times) / len(mp_times)
            mp_avg_retrieve = sum(t[1] for t in mp_times) / len(mp_times)

            sd_avg_store = sum(t[0] for t in sd_times) / len(sd_times)
            sd_avg_retrieve = sum(t[1] for t in sd_times) / len(sd_times)

            print(f"\n📊 Results for {config['name']}:")
            print(
                f"  MP Dict     - Store: {mp_avg_store * 1000:.2f}ms, Retrieve: {mp_avg_retrieve * 1000:.2f}ms, Total: {mp_total_time:.2f}s"
            )
            print(
                f"  SharedDict  - Store: {sd_avg_store * 1000:.2f}ms, Retrieve: {sd_avg_retrieve * 1000:.2f}ms, Total: {sd_total_time:.2f}s"
            )

            store_speedup = mp_avg_store / sd_avg_store if sd_avg_store > 0 else 0
            retrieve_speedup = (
                mp_avg_retrieve / sd_avg_retrieve if sd_avg_retrieve > 0 else 0
            )
            total_speedup = mp_total_time / sd_total_time if sd_total_time > 0 else 0

            print("\n🏆 Performance Improvements:")
            print(f"  Store Operations: {store_speedup:.2f}x faster")
            print(f"  Retrieve Operations: {retrieve_speedup:.2f}x faster")
            print(f"  Total Time: {total_speedup:.2f}x faster")

            # Calculate operation throughput
            total_ops = (
                len(sd_times) * 20 * 2
            )  # 20 objects per iteration, store + retrieve
            mp_ops_per_sec = total_ops / mp_total_time if mp_total_time > 0 else 0
            sd_ops_per_sec = total_ops / sd_total_time if sd_total_time > 0 else 0

            print("\n📈 Throughput:")
            print(f"  MP Dict: {mp_ops_per_sec:.0f} operations/sec")
            print(f"  SharedDict: {sd_ops_per_sec:.0f} operations/sec")

            results_summary.append(
                {
                    "config": config["name"],
                    "numpy_ratio": config["numpy_ratio"],
                    "store_speedup": store_speedup,
                    "retrieve_speedup": retrieve_speedup,
                    "total_speedup": total_speedup,
                    "sd_ops_per_sec": sd_ops_per_sec,
                    "mp_ops_per_sec": mp_ops_per_sec,
                }
            )

        # Cleanup
        d.unlink()
        manager.shutdown()

        print("-" * 60)

    # Print comprehensive summary
    print(f"\n{'=' * 80}")
    print("COMPREHENSIVE BENCHMARK SUMMARY")
    print("=" * 80)

    for result in results_summary:
        print(f"\n{result['config']} (Numpy Ratio: {result['numpy_ratio']:.1f}):")
        print(f"  Store Speed Improvement: {result['store_speedup']:.2f}x")
        print(f"  Retrieve Speed Improvement: {result['retrieve_speedup']:.2f}x")
        print(f"  Overall Speed Improvement: {result['total_speedup']:.2f}x")
        print(
            f"  Throughput: {result['sd_ops_per_sec']:.0f} vs {result['mp_ops_per_sec']:.0f} ops/sec"
        )

    # Analyze trends based on numpy ratio
    print("\n🔍 ANALYSIS BY NUMPY CONTENT RATIO:")

    for result in results_summary:
        numpy_pct = result["numpy_ratio"] * 100
        improvement = result["total_speedup"]
        if improvement > 2.0:
            performance_desc = "Excellent"
        elif improvement > 1.5:
            performance_desc = "Good"
        elif improvement > 1.1:
            performance_desc = "Moderate"
        else:
            performance_desc = "Minimal"

        print(
            f"  {numpy_pct:3.0f}% Numpy: {improvement:.2f}x faster - {performance_desc} improvement"
        )

    # Overall averages
    avg_store_speedup = sum(r["store_speedup"] for r in results_summary) / len(
        results_summary
    )
    avg_retrieve_speedup = sum(r["retrieve_speedup"] for r in results_summary) / len(
        results_summary
    )
    avg_total_speedup = sum(r["total_speedup"] for r in results_summary) / len(
        results_summary
    )

    print("\n🎯 OVERALL AVERAGES ACROSS ALL RATIOS:")
    print(f"  Average Store Speedup: {avg_store_speedup:.2f}x")
    print(f"  Average Retrieve Speedup: {avg_retrieve_speedup:.2f}x")
    print(f"  Average Total Speedup: {avg_total_speedup:.2f}x")

    print("\n✨ CONCLUSION:")
    if avg_total_speedup > 1.5:
        print(
            f"  SharedDict shows strong performance advantages ({avg_total_speedup:.2f}x faster overall)"
        )
        print(
            "  across mixed workloads, with benefits for both numpy arrays and built-in types."
        )
        print(
            "  The custom numpy serialization provides significant improvements even in mixed scenarios."
        )
    elif avg_total_speedup > 1.1:
        print(
            f"  SharedDict shows moderate performance improvements ({avg_total_speedup:.2f}x faster overall)"
        )
        print(
            "  in mixed data scenarios, with best results when numpy content is higher."
        )
    else:
        print("  Performance is comparable between the two approaches for mixed data.")

    print("=" * 80)


if __name__ == "__main__":
    try:
        benchmark_mixed_data_performance()
    except KeyboardInterrupt:
        print("\n\nBenchmark interrupted by user.")
    except Exception as e:
        print(f"\n\nBenchmark failed with error: {e}")
        import traceback

        traceback.print_exc()
