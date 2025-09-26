#!/usr/bin/env python3
"""
Numpy Performance Benchmark: SharedDict vs multiprocessing.Manager().dict()

This benchmark compares the performance of numpy array storage and retrieval
between SharedDict (with custom numpy serialization) and Python's built-in
multiprocessing.Manager().dict() (which uses pickle).
"""

import multiprocessing as mp
import time
from typing import Any

import numpy as np

from sharedbox import SharedDict


def mp_dict_worker(
    shared_dict: Any,
    worker_id: int,
    iterations: int,
    array_size: tuple[int, int],
    results_queue: Any,
) -> None:
    """Worker function for multiprocessing.Manager().dict() performance test"""
    print(f"  MP Dict Worker {worker_id}: Starting {iterations} operations...")

    times: list[tuple[float, float]] = []
    for i in range(iterations):
        # Create array and convert to list for pickle serialization
        arr = np.random.rand(*array_size).astype(np.float32)
        arr_list = arr.tolist()  # Convert to list for pickle

        # Time store operation
        start = time.perf_counter()
        shared_dict[f"worker_{worker_id}_array_{i}"] = arr_list
        store_time = time.perf_counter() - start

        # Time retrieve operation
        start = time.perf_counter()
        _ = shared_dict[f"worker_{worker_id}_array_{i}"]
        retrieve_time = time.perf_counter() - start

        times.append((store_time, retrieve_time))

    print(f"  MP Dict Worker {worker_id}: Completed")
    results_queue.put((worker_id, times))


def shareddict_worker(
    worker_id: int, iterations: int, array_size: tuple[int, int], results_queue: Any
) -> None:
    """Worker function for SharedDict performance test"""
    print(f"  SharedDict Worker {worker_id}: Starting {iterations} operations...")

    d = SharedDict("numpy_perf_benchmark", create=False)
    times: list[tuple[float, float]] = []

    for i in range(iterations):
        # Create numpy array (stays as numpy array)
        arr = np.random.rand(*array_size).astype(np.float32)

        # Time store operation
        start = time.perf_counter()
        d[f"worker_{worker_id}_array_{i}"] = arr
        store_time = time.perf_counter() - start

        # Time retrieve operation
        start = time.perf_counter()
        _ = d[f"worker_{worker_id}_array_{i}"]
        retrieve_time = time.perf_counter() - start

        times.append((store_time, retrieve_time))

    print(f"  SharedDict Worker {worker_id}: Completed")
    results_queue.put((worker_id, times))


def benchmark_numpy_performance() -> None:
    """Run comprehensive numpy performance benchmark"""
    print("=" * 80)
    print("NUMPY ARRAY PERFORMANCE BENCHMARK")
    print(
        "SharedDict (custom serialization) vs multiprocessing.Manager().dict() (pickle)"
    )
    print("=" * 80)

    # Test parameters
    test_configs: list[dict[str, Any]] = [
        {"name": "Small Arrays", "size": (50, 50), "workers": 2, "iterations": 10},
        {"name": "Medium Arrays", "size": (200, 200), "workers": 2, "iterations": 5},
        {"name": "Large Arrays", "size": (500, 500), "workers": 2, "iterations": 3},
    ]

    results_summary: list[dict[str, Any]] = []

    for config in test_configs:
        print(f"\n{config['name']} Test:")
        print(
            f"  Array Size: {config['size']} ({np.prod(config['size']) * 4 / 1024 / 1024:.1f} MB)"
        )
        print(
            f"  Workers: {config['workers']}, Iterations per worker: {config['iterations']}"
        )

        # Test multiprocessing.Manager().dict()
        print("\n Testing multiprocessing.Manager().dict() (pickle serialization):")
        manager = mp.Manager()
        mp_dict = manager.dict()
        mp_queue = manager.Queue()

        mp_start = time.perf_counter()

        mp_processes = []
        for worker_id in range(config["workers"]):
            p = mp.Process(
                target=mp_dict_worker,
                args=(
                    mp_dict,
                    worker_id,
                    config["iterations"],
                    config["size"],
                    mp_queue,
                ),
            )
            p.start()
            mp_processes.append(p)

        for p in mp_processes:
            p.join(timeout=60)

        mp_end = time.perf_counter()
        mp_total_time = mp_end - mp_start

        # Collect MP results
        mp_times: list[tuple[float, float]] = []
        while not mp_queue.empty():
            worker_id, times = mp_queue.get()
            mp_times.extend(times)

        # Test SharedDict
        print("\n Testing SharedDict (custom numpy serialization):")

        # Calculate memory size needed
        array_bytes = np.prod(config["size"]) * 4  # float32 = 4 bytes
        total_arrays = config["workers"] * config["iterations"]
        memory_needed = total_arrays * array_bytes * 2  # 2x for safety
        memory_mb = max(50, int(memory_needed / 1024 / 1024))

        d = SharedDict(
            "numpy_perf_benchmark",
            size=memory_mb * 1024 * 1024,
            create=True,
            max_keys=128,
        )
        sd_queue = manager.Queue()

        sd_start = time.perf_counter()

        sd_processes = []
        for worker_id in range(config["workers"]):
            p = mp.Process(
                target=shareddict_worker,
                args=(worker_id, config["iterations"], config["size"], sd_queue),
            )
            p.start()
            sd_processes.append(p)

        for p in sd_processes:
            p.join(timeout=60)

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

            print(f"\n Results for {config['name']}:")
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

            print("\n Performance Improvements:")
            print(f"  Store Operations: {store_speedup:.2f}x faster")
            print(f"  Retrieve Operations: {retrieve_speedup:.2f}x faster")
            print(f"  Total Time: {total_speedup:.2f}x faster")

            # Calculate throughput
            array_mb = np.prod(config["size"]) * 4 / 1024 / 1024
            total_ops = len(sd_times)

            mp_throughput = (array_mb * total_ops) / mp_total_time
            sd_throughput = (array_mb * total_ops) / sd_total_time

            print("\n Throughput:")
            print(f"  MP Dict: {mp_throughput:.1f} MB/s")
            print(f"  SharedDict: {sd_throughput:.1f} MB/s")

            results_summary.append(
                {
                    "config": config["name"],
                    "store_speedup": store_speedup,
                    "retrieve_speedup": retrieve_speedup,
                    "total_speedup": total_speedup,
                    "sd_throughput": sd_throughput,
                    "mp_throughput": mp_throughput,
                }
            )

        # Cleanup
        d.close()
        d.unlink()
        manager.shutdown()

        print("-" * 60)

    # Print summary
    print(f"\n{'=' * 80}")
    print("BENCHMARK SUMMARY")
    print("=" * 80)

    for result in results_summary:
        print(f"\n{result['config']}:")
        print(f"  Store Speed Improvement: {result['store_speedup']:.2f}x")
        print(f"  Retrieve Speed Improvement: {result['retrieve_speedup']:.2f}x")
        print(f"  Overall Speed Improvement: {result['total_speedup']:.2f}x")
        print(
            f"  Throughput: {result['sd_throughput']:.1f} MB/s vs {result['mp_throughput']:.1f} MB/s"
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

    print("\n OVERALL AVERAGES:")
    print(f"  Average Store Speedup: {avg_store_speedup:.2f}x")
    print(f"  Average Retrieve Speedup: {avg_retrieve_speedup:.2f}x")
    print(f"  Average Total Speedup: {avg_total_speedup:.2f}x")

    print("\n CONCLUSION:")
    if avg_total_speedup > 1:
        print(
            f"  SharedDict is {avg_total_speedup:.2f}x faster overall than multiprocessing.Manager().dict()"
        )
        print(
            "  for numpy array operations, thanks to custom binary serialization vs pickle."
        )
    else:
        print("  Results show comparable performance between the two approaches.")

    print("=" * 80)


if __name__ == "__main__":
    try:
        benchmark_numpy_performance()
    except KeyboardInterrupt:
        print("\n\nBenchmark interrupted by user.")
    except Exception as e:
        print(f"\n\nBenchmark failed with error: {e}")
        import traceback

        traceback.print_exc()
