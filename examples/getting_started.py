"""
Simple getting started example showing basic SharedDict usage.

This example demonstrates the basic API and shows a simple performance comparison.

Run with: python examples/getting_started.py
"""

import multiprocessing as mp
import time
from typing import Any, Dict

from sharedbox import SharedDict


def worker_shareddict(segment_name: str, worker_id: int) -> None:
    """Simple worker that writes to SharedDict."""
    # Connect to existing shared memory
    d = SharedDict(segment_name, create=False)

    # Write some data
    d[f"worker_{worker_id}"] = {
        "message": f"Hello from worker {worker_id}",
        "timestamp": time.time(),
        "data": list(range(10)),
    }

    # Read data from other workers
    for key in d:
        if key.startswith("worker_") and key != f"worker_{worker_id}":
            other_data = d[key]
            print(f"Worker {worker_id} sees: {other_data['message']}")

    # Close connection in child process
    d.close()


def worker_manager(manager_dict: Dict[str, Any], worker_id: int) -> None:
    """Simple worker that writes to Manager dict."""
    # Write some data
    manager_dict[f"worker_{worker_id}"] = {
        "message": f"Hello from worker {worker_id}",
        "timestamp": time.time(),
        "data": list(range(10)),
    }

    # Try to read data from other workers (with error handling)
    # Note: Manager dict iteration can be unreliable in some scenarios
    try:
        for other_worker in range(3):
            if other_worker != worker_id:
                key = f"worker_{other_worker}"
                if key in manager_dict:
                    other_data = manager_dict[key]
                    print(f"Worker {worker_id} sees: {other_data['message']}")
    except (AttributeError, KeyError):
        # Manager dict access can fail during shutdown
        pass


def basic_usage_example() -> None:
    """Demonstrate basic SharedDict usage."""
    print("=== Basic SharedDict Usage ===")

    # Create a SharedDict
    segment_name = "getting_started_example"
    segment_size = 10 * 1024 * 1024  # 10MB

    d = SharedDict(segment_name, size=segment_size, create=True)

    # Basic operations
    print("\n1. Basic Operations:")
    d["hello"] = "world"
    d["numbers"] = [1, 2, 3, 4, 5]
    d["nested"] = {"key": "value", "count": 42}

    print(f"   d['hello'] = {d['hello']}")
    print(f"   d['numbers'] = {d['numbers']}")
    print(f"   d['nested'] = {d['nested']}")
    print(f"   Size: {len(d)} items")

    # Iteration
    print("\n2. Iteration:")
    for key in d:
        print(f"   {key}: {d[key]}")

    # Check if key exists
    print("\n3. Key checking:")
    print(f"   'hello' in d: {'hello' in d}")
    print(f"   'missing' in d: {'missing' in d}")

    # Delete items
    print("\n4. Deletion:")
    del d["hello"]
    print(f"   After deleting 'hello': {list(d.keys())}")

    # Clean up
    d.unlink()
    print("\n5. Cleaned up shared memory")


def simple_multiprocess_example() -> None:
    """Simple multiprocess example."""
    print("\n=== Simple Multiprocess Example ===")

    # SharedDict example
    print("\nSharedDict multiprocess:")
    segment_name = "simple_mp_test"

    d = SharedDict(segment_name, size=10 * 1024 * 1024, create=True)
    d["main_message"] = "Hello from main process!"

    # Start 3 worker processes
    processes = []
    for worker_id in range(3):
        p = mp.Process(target=worker_shareddict, args=(segment_name, worker_id))
        p.start()
        processes.append(p)

    # Wait for completion
    for p in processes:
        p.join()

    print(f"Final SharedDict contents: {len(d)} items")
    for key in d:
        if key.startswith("worker_"):
            print(f"  {key}: {d[key]['message']}")
        else:
            print(f"  {key}: {d[key]}")

    d.unlink()

    # Manager dict example for comparison
    print("\nManager dict multiprocess:")

    with mp.Manager() as manager:
        manager_dict = manager.dict()
        manager_dict["main_message"] = "Hello from main process!"

        # Start 3 worker processes
        processes = []
        for worker_id in range(3):
            p = mp.Process(target=worker_manager, args=(manager_dict, worker_id))
            p.start()
            processes.append(p)

        # Wait for completion
        for p in processes:
            p.join()

        print(f"Final Manager dict contents: {len(manager_dict)} items")
        for key in manager_dict:
            if key.startswith("worker_"):
                print(f"  {key}: {manager_dict[key]['message']}")
            else:
                print(f"  {key}: {manager_dict[key]}")


def performance_comparison() -> None:
    """Simple performance comparison."""
    print("\n=== Simple Performance Comparison ===")

    num_operations = 1000

    # Test SharedDict performance
    print(f"\nTesting SharedDict with {num_operations} operations...")

    segment_name = "perf_test"
    d = SharedDict(segment_name, size=50 * 1024 * 1024, create=True)

    start_time = time.perf_counter()

    # Write operations
    for i in range(num_operations):
        d[f"key_{i}"] = {"value": i, "squared": i**2, "text": f"item_{i}"}

    write_time = time.perf_counter() - start_time

    # Read operations
    start_time = time.perf_counter()

    for i in range(num_operations):
        _ = d[f"key_{i}"]

    read_time = time.perf_counter() - start_time

    d.unlink()

    print(f"  Write: {write_time:.3f}s ({num_operations / write_time:.1f} ops/sec)")
    print(f"  Read:  {read_time:.3f}s ({num_operations / read_time:.1f} ops/sec)")

    # Test Manager dict performance
    print(f"\nTesting Manager dict with {num_operations} operations...")

    with mp.Manager() as manager:
        manager_dict = manager.dict()

        start_time = time.perf_counter()

        # Write operations
        for i in range(num_operations):
            manager_dict[f"key_{i}"] = {
                "value": i,
                "squared": i**2,
                "text": f"item_{i}",
            }

        write_time = time.perf_counter() - start_time

        # Read operations
        start_time = time.perf_counter()

        for i in range(num_operations):
            _ = manager_dict[f"key_{i}"]

        read_time = time.perf_counter() - start_time

    print(f"  Write: {write_time:.3f}s ({num_operations / write_time:.1f} ops/sec)")
    print(f"  Read:  {read_time:.3f}s ({num_operations / read_time:.1f} ops/sec)")


def memory_management_example() -> None:
    """Demonstrate proper memory management."""
    print("\n=== Memory Management ===")

    segment_name = "memory_mgmt_test"

    # Create SharedDict
    d = SharedDict(segment_name, size=10 * 1024 * 1024, create=True)
    d["test_data"] = "This is test data"

    print(f"1. Created SharedDict with segment name: '{segment_name}'")
    print(f"   is_closed(): {d.is_closed()}")

    # Close (for child processes)
    d.close()
    print(f"2. Called close() - is_closed(): {d.is_closed()}")

    # Try to access after close (should raise exception)
    try:
        _ = d["test_data"]
        print("   ERROR: Access after close() should have failed!")
    except RuntimeError as e:
        print(f"   ✓ Correctly blocked access: {e}")

    # Unlink (clean up shared memory)
    d.unlink()
    print("3. Called unlink() - shared memory cleaned up")

    print("\nMemory Management Best Practices:")
    print("  • Child processes should call close() when done")
    print("  • Main/creating process should call unlink() for cleanup")
    print("  • Only unlink() actually removes the shared memory")
    print("  • Similar to multiprocessing.SharedMemory API")


def main() -> None:
    """Main function running all examples."""
    print("SharedDict Getting Started Examples")
    print("=" * 40)

    # Basic usage
    basic_usage_example()

    # Simple multiprocess
    simple_multiprocess_example()

    # Performance comparison
    performance_comparison()

    # Memory management
    memory_management_example()

    print("\n" + "=" * 40)
    print("Summary:")
    print("✓ SharedDict provides dict-like API for shared memory")
    print("✓ Better performance than multiprocessing.Manager")
    print("✓ Proper memory management with close()/unlink()")
    print("✓ Supports all picklable Python objects")
    print("\nNext steps: Try the detailed examples in this folder!")


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
