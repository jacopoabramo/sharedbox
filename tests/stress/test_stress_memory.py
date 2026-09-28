import gc
import sys
import threading
import time
import tracemalloc
import uuid
from collections.abc import Callable
from typing import Annotated

import pytest
from stress_helpers import (
    leftovers,
    open_handles,
    percentiles,
    rss_bytes,
    scale,
    scaled,
)

from sharedbox import Capacity, SharedBox

pytestmark = pytest.mark.stress

MIB = 1 << 20


class Cycle(SharedBox):
    value: int = 0
    label: Annotated[str, Capacity(32)] = ""


class Blob(SharedBox):
    data: Annotated[bytes, Capacity(1 << 20)] = b""


class Small(SharedBox):
    data: Annotated[bytes, Capacity(16)] = b""


def one_cycle(name: str) -> None:
    """Create, attach, write, watch one change and close a box."""
    delivered = threading.Event()
    try:
        with Cycle.create(name) as box, Cycle.attach(name) as other:
            other.events.value.connect(lambda new, old: delivered.set())
            box.update(value=1, label="cycle")
            assert delivered.wait(10)
    finally:
        Cycle.unlink(name)


def test_create_attach_watch_close_cycles_hold_memory_steady(
    report: Callable[[dict[str, object]], None],
) -> None:
    prefix = f"sbstress-{uuid.uuid4().hex[:8]}"
    cycles, limit = scaled(10_000), 60 * scale()
    warm_up = min(500, cycles // 10 + 1)
    for i in range(warm_up):
        one_cycle(f"{prefix}-w{i}")
    gc.collect()
    rss_start, handles_start = rss_bytes(), open_handles()
    start = time.monotonic()
    done = 0
    while done < cycles and time.monotonic() - start < limit:
        one_cycle(f"{prefix}-{done}")
        done += 1
    gc.collect()
    time.sleep(0.5)
    growth = rss_bytes() - rss_start
    handle_growth = open_handles() - handles_start
    report(
        {
            "cycles": done,
            "seconds": time.monotonic() - start,
            "rss_growth_mib": growth / MIB,
            "handle_growth": handle_growth,
            "shm_leftovers": len(leftovers(prefix)),
        }
    )
    assert growth < 10 * MIB
    # A few handles come and go with threads and the allocator; a leak grows with the cycles.
    assert handle_growth < 16
    assert leftovers(prefix) == []


def test_a_one_mib_field(
    unique_name: str, report: Callable[[dict[str, object]], None]
) -> None:
    payload = bytes(range(256)) * 4096
    seconds = 2 * scale()
    with Blob.create(unique_name) as box:
        writes = 0
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            box.data = payload
            writes += 1
        write_rate = writes / (time.monotonic() - start)
        reads = 0
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            assert len(box.data) == MIB
            reads += 1
        read_rate = reads / (time.monotonic() - start)

        box.data = b"ten bytes."
        tracemalloc.start()
        tracemalloc.reset_peak()
        assert box.data == b"ten bytes."
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # tracemalloc sees only Python's allocations; the native read's own buffer shows in time:
        # zero-filling the 1 MiB capacity costs tens of microseconds, a 10-byte copy nanoseconds.
        big = [0] * 2000
        for i in range(len(big)):
            t = time.perf_counter_ns()
            box.data  # noqa: B018
            big[i] = time.perf_counter_ns() - t
    with Small.create(f"{unique_name}-small") as small:
        small.data = b"ten bytes."
        little = [0] * 2000
        for i in range(len(little)):
            t = time.perf_counter_ns()
            small.data  # noqa: B018
            little[i] = time.perf_counter_ns() - t
    Small.unlink(f"{unique_name}-small")
    ratio = sorted(big)[len(big) // 2] / max(1, sorted(little)[len(little) // 2])
    report(
        {
            "writes_per_s": write_rate,
            "reads_per_s": read_rate,
            "read_10_bytes_python_peak_bytes": peak,
            "read_10_bytes_in_1_mib_us": percentiles(big),
            "read_10_bytes_in_16_bytes_us": percentiles(little),
            "median_ratio": ratio,
        }
    )
    assert peak < 64 * 1024
    assert ratio < 20


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the file-descriptor limit this measures exists on Linux",
)
def test_nine_hundred_open_boxes(report: Callable[[dict[str, object]], None]) -> None:
    prefix = f"sbstress-{uuid.uuid4().hex[:8]}"
    count = 900
    rss_start, fds_start = rss_bytes(), open_handles()
    start = time.monotonic()
    boxes = [Cycle.create(f"{prefix}-{i}") for i in range(count)]
    opened = time.monotonic() - start
    rss_open, fds_open = rss_bytes(), open_handles()
    for i, box in enumerate(boxes):
        box.close()
        Cycle.unlink(f"{prefix}-{i}")
    report(
        {
            "boxes": count,
            "open_seconds": opened,
            "rss_per_box_kib": (rss_open - rss_start) / count / 1024,
            "fds_per_box": (fds_open - fds_start) / count,
            "fds_after_close": open_handles() - fds_start,
            "shm_leftovers": len(leftovers(prefix)),
        }
    )
    assert count <= fds_open - fds_start <= count + 8
    assert open_handles() - fds_start <= 1
    assert leftovers(prefix) == []
