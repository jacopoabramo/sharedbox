import math
import pickle
from typing import Any


class SegmentSizer:
    """Helper class for calculating optimal shared memory segment sizes."""

    # Conservative estimates for overhead per entry
    MAP_NODE_OVERHEAD = 128  # bytes per map entry for pointers, alignment, etc.
    ALLOCATOR_OVERHEAD_RATIO = 0.05  # 5% of total segment for allocator metadata
    MUTEX_SIZE = 96  # bytes per interprocess_mutex (conservative estimate)

    @classmethod
    def estimate_pickle_size(cls, obj: Any) -> int:
        """Estimate the pickle size of an object."""
        try:
            return len(pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL))
        except Exception:
            # Fallback for unpicklable objects
            return len(str(obj).encode("utf-8"))

    @classmethod
    def calculate_segment_size(
        cls,
        num_entries: int,
        avg_key_size: int,
        avg_value_size: int,
        num_locks: int = 2048,
        safety_margin: float = 1.5,
    ) -> dict[str, int]:
        """
        Calculate recommended segment size based on workload characteristics.

        Args:
            num_entries: Expected number of dictionary entries
            avg_key_size: Average size of pickled keys in bytes
            avg_value_size: Average size of pickled values in bytes
            num_locks: Number of lock stripes (default 2048)
            safety_margin: Multiplier for safety/fragmentation (default 1.5x)

        Returns:
            dict with size breakdown and recommendation
        """
        # Calculate component sizes
        data_size: int = num_entries * (avg_key_size + avg_value_size)
        entry_overhead: int = num_entries * cls.MAP_NODE_OVERHEAD
        locks_overhead: int = num_locks * cls.MUTEX_SIZE

        # Base size before safety margin
        base_size: int = data_size + entry_overhead + locks_overhead

        # Add allocator overhead
        allocator_overhead: int = int(base_size * cls.ALLOCATOR_OVERHEAD_RATIO)
        base_size += allocator_overhead

        # Apply safety margin for fragmentation and growth
        recommended_size: int = int(base_size * safety_margin)

        # Round up to next power of 2 for better allocation
        recommended_size_pow2: int = 2 ** math.ceil(
            math.log2(max(recommended_size, 1024 * 1024))
        )

        return {
            "data_bytes": data_size,
            "entry_overhead_bytes": entry_overhead,
            "locks_overhead_bytes": locks_overhead,
            "allocator_overhead_bytes": allocator_overhead,
            "base_size_bytes": base_size,
            "recommended_size_bytes": recommended_size,
            "recommended_size_pow2_bytes": recommended_size_pow2,
            "safety_margin": safety_margin,
        }

    @classmethod
    def size_for_workload(cls, workload_type: str) -> dict[str, Any]:
        """
        Get pre-calculated size recommendations for common workload patterns.

        Args:
            workload_type: One of 'small', 'medium', 'large', 'xlarge'

        Returns:
            dict with workload parameters and size recommendations
        """
        workloads = {
            "small": {
                "description": "1K entries, ~100 bytes avg per key+value",
                "num_entries": 1_000,
                "avg_key_size": 50,
                "avg_value_size": 50,
                "num_locks": 1024,
            },
            "medium": {
                "description": "100K entries, ~500 bytes avg per key+value",
                "num_entries": 100_000,
                "avg_key_size": 100,
                "avg_value_size": 400,
                "num_locks": 2048,
            },
            "large": {
                "description": "1M entries, ~1KB avg per key+value",
                "num_entries": 1_000_000,
                "avg_key_size": 200,
                "avg_value_size": 800,
                "num_locks": 8192,
            },
            "xlarge": {
                "description": "10M entries, ~2KB avg per key+value",
                "num_entries": 10_000_000,
                "avg_key_size": 400,
                "avg_value_size": 1600,
                "num_locks": 16384,
            },
        }

        if workload_type not in workloads:
            raise ValueError(
                f"Unknown workload type '{workload_type}'. "
                f"Available: {list(workloads.keys())}"
            )

        params = workloads[workload_type]
        sizing = cls.calculate_segment_size(
            params["num_entries"],
            params["avg_key_size"],
            params["avg_value_size"],
            params["num_locks"],
        )

        return {**params, **sizing}

    @classmethod
    def analyze_sample_data(
        cls, sample_keys: list[Any], sample_values: list[Any]
    ) -> dict[str, Any]:
        """
        Analyze sample data to estimate sizing requirements.

        Args:
            sample_keys: Representative sample of keys
            sample_values: Representative sample of values

        Returns:
            dict with analysis results and size recommendations
        """
        if len(sample_keys) != len(sample_values):
            raise ValueError("sample_keys and sample_values must have same length")

        if not sample_keys:
            raise ValueError("Empty samples provided")

        # Calculate pickle sizes
        key_sizes = [cls.estimate_pickle_size(k) for k in sample_keys]
        value_sizes = [cls.estimate_pickle_size(v) for v in sample_values]

        # Statistical analysis
        avg_key_size = sum(key_sizes) // len(key_sizes)
        avg_value_size = sum(value_sizes) // len(value_sizes)
        max_key_size = max(key_sizes)
        max_value_size = max(value_sizes)

        # Generate recommendations for different scales
        recommendations = {}
        for scale, multiplier in [
            ("current", 1),
            ("10x", 10),
            ("100x", 100),
            ("1000x", 1000),
        ]:
            num_entries = len(sample_keys) * multiplier
            sizing = cls.calculate_segment_size(
                num_entries, avg_key_size, avg_value_size
            )
            recommendations[scale] = sizing

        return {
            "sample_size": len(sample_keys),
            "avg_key_size_bytes": avg_key_size,
            "avg_value_size_bytes": avg_value_size,
            "max_key_size_bytes": max_key_size,
            "max_value_size_bytes": max_value_size,
            "key_sizes": key_sizes,
            "value_sizes": value_sizes,
            "recommendations": recommendations,
        }


class LockTuner:
    """Helper class for lock count tuning and performance optimization."""

    @classmethod
    def recommend_lock_count(
        cls,
        num_entries: int,
        write_concurrency: int = 4,
        target_entries_per_lock: int = 500,
    ) -> dict[str, Any]:
        """
        Recommend optimal lock count based on workload characteristics.

        Args:
            num_entries: Expected number of dictionary entries
            write_concurrency: Expected number of concurrent writers
            target_entries_per_lock: Target entries per lock stripe

        Returns:
            dict with recommendations and rationale
        """
        # Calculate lock count based on entries per lock target
        locks_for_entries: int = max(64, num_entries // target_entries_per_lock)

        # Ensure enough locks for write concurrency (at least 4x writers)
        locks_for_concurrency: int = write_concurrency * 4

        # Take the maximum and round to next power of 2
        min_locks: int = max(locks_for_entries, locks_for_concurrency, 64)
        recommended_locks: int = 2 ** math.ceil(math.log2(min_locks))

        # Cap at reasonable maximum (too many locks waste memory)
        max_reasonable = 65536
        if recommended_locks > max_reasonable:
            recommended_locks = max_reasonable

        # Calculate actual entries per lock with recommendation
        actual_entries_per_lock = (
            num_entries / recommended_locks if recommended_locks else 0
        )

        # Memory cost of locks
        lock_memory_kb = (recommended_locks * SegmentSizer.MUTEX_SIZE) // 1024

        return {
            "recommended_lock_count": recommended_locks,
            "actual_entries_per_lock": actual_entries_per_lock,
            "lock_memory_kb": lock_memory_kb,
            "rationale": {
                "target_entries_per_lock": target_entries_per_lock,
                "locks_for_entries": locks_for_entries,
                "locks_for_concurrency": locks_for_concurrency,
                "write_concurrency": write_concurrency,
            },
        }

    @classmethod
    def performance_presets(cls) -> dict[str, dict[str, Any]]:
        """
        Get pre-configured lock count recommendations for different performance profiles.

        Returns:
            dict mapping preset names to lock configurations
        """
        return {
            "memory_optimized": {
                "description": "Minimal memory usage, lower concurrency",
                "base_locks": 256,
                "max_locks": 1024,
                "entries_per_lock": 2000,
                "suitable_for": "Memory-constrained environments, low write concurrency",
            },
            "balanced": {
                "description": "Good balance of memory and performance",
                "base_locks": 1024,
                "max_locks": 8192,
                "entries_per_lock": 500,
                "suitable_for": "Most general-purpose applications",
            },
            "performance_optimized": {
                "description": "Maximum concurrency, higher memory usage",
                "base_locks": 4096,
                "max_locks": 32768,
                "entries_per_lock": 100,
                "suitable_for": "High-concurrency, write-heavy workloads",
            },
            "extreme_performance": {
                "description": "Ultra-high concurrency for specialized use cases",
                "base_locks": 16384,
                "max_locks": 65536,
                "entries_per_lock": 50,
                "suitable_for": "Specialized high-throughput applications",
            },
        }


def format_size(size_bytes: int) -> str:
    """Format byte size in human-readable format."""
    size_float: float = size_bytes
    for unit in ["B", "KB", "MB", "GB"]:
        if size_float < 1024:
            return f"{size_float:.1f}{unit}"
        size_float /= 1024
    return f"{size_float:.1f}TB"
