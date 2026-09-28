import multiprocessing as mp
import time
from array import array
from collections.abc import Callable
from multiprocessing.queues import Queue

import pytest
from stress_helpers import percentiles, scale, scaled

from sharedbox import LockTimeoutError, SharedBox

pytestmark = pytest.mark.stress

# Every 16th latency is kept: enough for p99.9, and ten seconds of reads stay tens of MiB.
STRIDE = 16


class Pair(SharedBox):
    a: int = 0
    b: int = 0


def write_until(
    name: str, seconds: float, out: "Queue[tuple[bytes, int, int]]"
) -> None:
    box = Pair.attach(name)
    latencies = array("q")
    timeouts = 0
    deadline = time.monotonic() + seconds
    i = 0
    while time.monotonic() < deadline:
        i += 1
        start = time.perf_counter_ns()
        try:
            box.update(a=i, b=-i)
        except LockTimeoutError:
            timeouts += 1
        if i % STRIDE == 0:
            latencies.append(time.perf_counter_ns() - start)
    box.close()
    out.put((latencies.tobytes(), i, timeouts))


def read_until(
    name: str, seconds: float, out: "Queue[tuple[bytes, int, int, int]]"
) -> None:
    box = Pair.attach(name)
    latencies = array("q")
    reads = timeouts = torn = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        start = time.perf_counter_ns()
        try:
            values = box.snapshot()
        except LockTimeoutError:
            timeouts += 1
            continue
        reads += 1
        if reads % STRIDE == 0:
            latencies.append(time.perf_counter_ns() - start)
        # update() writes a and -a under one lock; a mix of two writes breaks this.
        if values["a"] != -values["b"]:
            torn += 1
    box.close()
    out.put((latencies.tobytes(), reads, timeouts, torn))


def test_writers_and_readers_on_one_box(
    unique_name: str, report: Callable[[dict[str, object]], None]
) -> None:
    writers, readers = 4, 8
    seconds = 10 * scale()
    context = mp.get_context("spawn")
    written: Queue[tuple[bytes, int, int]] = context.Queue()
    read: Queue[tuple[bytes, int, int, int]] = context.Queue()
    with Pair.create(unique_name):
        processes = [
            context.Process(target=write_until, args=(unique_name, seconds, written))
            for _ in range(writers)
        ] + [
            context.Process(target=read_until, args=(unique_name, seconds, read))
            for _ in range(readers)
        ]
        for process in processes:
            process.start()
        write_results = [written.get(timeout=seconds + 60) for _ in range(writers)]
        read_results = [read.get(timeout=seconds + 60) for _ in range(readers)]
        for process in processes:
            process.join(30)
    write_latencies = array("q")
    read_latencies = array("q")
    for data, *_ in write_results:
        write_latencies.frombytes(data)
    for data, *_ in read_results:
        read_latencies.frombytes(data)
    writes = sum(r[1] for r in write_results)
    reads = sum(r[1] for r in read_results)
    write_timeouts = sum(r[2] for r in write_results)
    read_timeouts = sum(r[2] for r in read_results)
    torn = sum(r[3] for r in read_results)
    report(
        {
            "writers": writers,
            "readers": readers,
            "seconds": seconds,
            "writes": writes,
            "reads": reads,
            "write_us": percentiles(write_latencies),
            "read_us": percentiles(read_latencies),
            "lock_timeouts": write_timeouts + read_timeouts,
            "torn_reads": torn,
        }
    )
    assert torn == 0
    assert write_timeouts + read_timeouts == 0
    assert writes >= scaled(1000)
