"""Helpers for the stress tests: scaling, percentiles, and what the process holds."""

import ctypes
import os
import sys
import threading
from collections.abc import Sequence


def scale() -> float:
    """``SHAREDBOX_STRESS_SCALE`` multiplies every duration and count; 1 gives the defaults."""
    return float(os.environ.get("SHAREDBOX_STRESS_SCALE", "1"))


def scaled(value: int) -> int:
    return max(1, round(value * scale()))


def percentiles(samples: Sequence[int]) -> dict[str, float]:
    """p50, p99, p99.9 and max of nanosecond samples, in microseconds."""
    ordered = sorted(samples)
    if not ordered:
        return {"p50": 0.0, "p99": 0.0, "p99.9": 0.0, "max": 0.0}

    def at(fraction: float) -> float:
        return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))] / 1000

    return {
        "p50": at(0.5),
        "p99": at(0.99),
        "p99.9": at(0.999),
        "max": ordered[-1] / 1000,
    }


def rss_bytes() -> int:
    """Resident memory of this process."""
    if sys.platform == "win32":

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.K32GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(Counters),
            ctypes.c_ulong,
        ]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel32.K32GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        )
        resident = int(counters.WorkingSetSize)
    else:
        with open("/proc/self/statm") as statm:
            resident = int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    return resident


def open_handles() -> int:
    """Open file descriptors on Linux, open handles on Windows."""
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.GetProcessHandleCount.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        count = ctypes.c_ulong()
        kernel32.GetProcessHandleCount(
            kernel32.GetCurrentProcess(), ctypes.byref(count)
        )
        handles = int(count.value)
    else:
        handles = len(os.listdir("/proc/self/fd"))
    return handles


def leftovers(prefix: str) -> list[str]:
    """Segments in /dev/shm whose box name starts with ``prefix``; none exist to find on Windows."""
    if not os.path.isdir("/dev/shm"):
        return []
    return sorted(
        n for n in os.listdir("/dev/shm") if n.startswith(f"sharedbox.{prefix}")
    )


def watcher_threads(name: str) -> int:
    """Live watcher threads of boxes called ``name`` in this process."""
    return sum(
        1
        for t in threading.enumerate()
        if t.name == f"sharedbox-watch-{name}" and t.is_alive()
    )
