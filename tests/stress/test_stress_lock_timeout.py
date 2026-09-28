import math
import multiprocessing as mp
import re
import statistics
import struct
import sys
import time
from array import array
from collections.abc import Callable
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Barrier, Event
from typing import Annotated

import pytest
from stress_helpers import percentiles, scale, scaled

from sharedbox import Capacity, LockTimeoutError, SharedBox
from sharedbox._native import Segment

pytestmark = pytest.mark.stress

MIB = 1 << 20
# Every 16th successful write's latency is kept, as in the contention test.
STRIDE = 16
# perf_counter and the lock's steady clock may round a deadline differently.
CLOCK_SLACK_NS = 1_000


class Milli(SharedBox, lock_timeout=0.001):
    value: int = 0


class Word(SharedBox, lock_timeout=0.01):
    data: int = 0


class Blob(SharedBox, lock_timeout=0.1):
    data: Annotated[bytes, Capacity(MIB)] = b""


class Held1(SharedBox, lock_timeout=1.0):
    value: int = 0


class Held5(SharedBox, lock_timeout=5.0):
    value: int = 0


def test_a_one_millisecond_lock_timeout(
    unique_name: str, report: Callable[[dict[str, object]], None]
) -> None:
    samples = scaled(200)
    waits: list[int] = []
    with Milli.create(unique_name) as holder, Milli.attach(unique_name) as other:
        holder._segment._hold_write_lock()
        for _ in range(samples):
            start = time.perf_counter_ns()
            with pytest.raises(LockTimeoutError):
                other.value = 1
            waits.append(time.perf_counter_ns() - start)
        holder._segment._release_held_lock()
        other.value = 2
        after_release = holder.value
    report(
        {
            "lock_timeout_s": 0.001,
            "samples": samples,
            "timeout_rate": 1.0,
            "wait_us": percentiles(waits),
            "overshoot_us": percentiles([w - 1_000_000 for w in waits]),
        }
    )
    assert min(waits) >= 1_000_000 - CLOCK_SLACK_NS
    # Linux sleeps about as long as asked; Windows ends the wait on a timer tick, 15.6 ms by default.
    assert statistics.median(waits) < (
        50_000_000 if sys.platform == "win32" else 5_000_000
    )
    assert after_release == 2


def value_for(writer: int, i: int, size: int) -> int | bytes:
    if size == 0:
        return writer << 32 | i
    return struct.pack("<II", writer, i) + bytes(size - 8)


def origin(value: int | bytes) -> tuple[int, int]:
    """The writer and sequence number that ``value_for`` put into a value."""
    if isinstance(value, int):
        return value >> 32, value & 0xFFFFFFFF
    writer, i = struct.unpack_from("<II", value)
    return writer, i


def write_for(
    cls: type[Word] | type[Blob],
    name: str,
    writer: int,
    size: int,
    seconds: float,
    start: Barrier,
    out: "Queue[tuple[int, bytes, bytes, int, int, int]]",
) -> None:
    box = cls.attach(name)
    timeout_ns = round(cls.__lock_timeout__ * 1e9)
    latencies = array("q")
    overshoots = array("q")
    writes = timeouts = last = 0
    start.wait(120)
    deadline = time.monotonic() + seconds
    i = 0
    while time.monotonic() < deadline:
        i += 1
        value = value_for(writer, i, size)
        began = time.perf_counter_ns()
        try:
            box.update(data=value)
        except LockTimeoutError:
            timeouts += 1
            overshoots.append(time.perf_counter_ns() - began - timeout_ns)
            continue
        took = time.perf_counter_ns() - began
        writes += 1
        last = i
        if writes % STRIDE == 0:
            latencies.append(took)
    box.close()
    out.put((writer, latencies.tobytes(), overshoots.tobytes(), writes, timeouts, last))


@pytest.mark.parametrize("writers", [2, 8, 32])
@pytest.mark.parametrize(
    ("cls", "size"), [(Word, 0), (Blob, MIB)], ids=["8_bytes_10ms", "1_mib_100ms"]
)
def test_timeout_rate_under_contention(
    cls: type[Word] | type[Blob],
    size: int,
    writers: int,
    unique_name: str,
    report: Callable[[dict[str, object]], None],
) -> None:
    seconds = 3 * scale()
    context = mp.get_context("spawn")
    start = context.Barrier(writers + 1)
    out: Queue[tuple[int, bytes, bytes, int, int, int]] = context.Queue()
    with cls.create(unique_name) as box:
        processes = [
            context.Process(
                target=write_for,
                args=(cls, unique_name, w, size, seconds, start, out),
            )
            for w in range(writers)
        ]
        for process in processes:
            process.start()
        start.wait(120)
        results = [out.get(timeout=seconds + 120) for _ in range(writers)]
        for process in processes:
            process.join(30)
        final = origin(box.snapshot()["data"])
    latencies = array("q")
    overshoots = array("q")
    for _, latency_bytes, overshoot_bytes, *_ in results:
        latencies.frombytes(latency_bytes)
        overshoots.frombytes(overshoot_bytes)
    writes = sum(r[3] for r in results)
    timeouts = sum(r[4] for r in results)
    report(
        {
            "writers": writers,
            "field_bytes": size or 8,
            "lock_timeout_s": cls.__lock_timeout__,
            "seconds": seconds,
            "writes": writes,
            "timeouts": timeouts,
            "timeout_rate": timeouts / max(1, writes + timeouts),
            "write_us": percentiles(latencies),
            "overshoot_us": percentiles(overshoots),
            "writers_without_a_write": sum(1 for r in results if r[5] == 0),
        }
    )
    # A write that timed out changed nothing: the box holds one writer's last successful write.
    assert final in {(r[0], r[5]) for r in results if r[5] != 0}
    assert min(overshoots, default=0) >= -CLOCK_SLACK_NS
    assert writes > 0
    if writers == 2:
        assert timeouts == 0


def hold_the_lock(cls: type[Held1] | type[Held5], name: str, ready: Event) -> None:
    box = cls.attach(name)
    box._segment._hold_write_lock()
    ready.set()
    time.sleep(600)


def one_access(
    cls: type[Held1] | type[Held5],
    name: str,
    kind: str,
    start: Barrier,
    out: "Queue[tuple[str, float, str]]",
) -> None:
    box = cls.attach(name)
    start.wait(120)
    began = time.perf_counter()
    message = ""
    try:
        if kind == "write":
            box.value = 1
        else:
            box.value  # noqa: B018
    except LockTimeoutError as error:
        message = str(error)
    out.put((kind, time.perf_counter() - began, message))
    box.close()


@pytest.mark.parametrize("cls", [Held1, Held5], ids=["1s", "5s"])
def test_a_writer_killed_holding_the_lock_times_out_everyone(
    cls: type[Held1] | type[Held5],
    unique_name: str,
    report: Callable[[dict[str, object]], None],
) -> None:
    lock_timeout = cls.__lock_timeout__
    kinds = ["write"] * 4 + ["read"] * 4
    context = mp.get_context("spawn")
    with cls.create(unique_name) as box:
        ready = context.Event()
        holder = context.Process(
            target=hold_the_lock, args=(cls, unique_name, ready), daemon=True
        )
        holder.start()
        assert ready.wait(60)
        holder.kill()
        holder.join(30)
        start = context.Barrier(len(kinds) + 1)
        out: Queue[tuple[str, float, str]] = context.Queue()
        processes = [
            context.Process(
                target=one_access, args=(cls, unique_name, kind, start, out)
            )
            for kind in kinds
        ]
        for process in processes:
            process.start()
        start.wait(120)
        results = [out.get(timeout=lock_timeout + 120) for _ in kinds]
        for process in processes:
            process.join(30)
        began = time.perf_counter()
        with pytest.raises(LockTimeoutError):
            box.value = 2
        still_locked = time.perf_counter() - began
        box.force_unlock()
        box.value = 3
        after_unlock = box.value
    waits = [round(waited * 1e9) for _, waited, _ in results]
    report(
        {
            "lock_timeout_s": lock_timeout,
            "accesses": len(kinds),
            "timeout_rate": sum(1 for *_, m in results if m) / len(kinds),
            "wait_us": percentiles(waits),
            "overshoot_us": percentiles([w - round(lock_timeout * 1e9) for w in waits]),
            "second_write_s": still_locked,
        }
    )
    for kind, waited, message in results:
        assert re.search(rf"locked by pid {holder.pid}\b", message), (kind, message)
        assert lock_timeout - 1e-6 <= waited < lock_timeout + 1.0, (kind, waited)
    assert still_locked >= lock_timeout - 1e-6
    assert after_unlock == 3


def test_the_longest_lock_timeout_is_accepted_and_the_next_double_refused(
    unique_name: str,
) -> None:
    class Day(SharedBox, lock_timeout=86400):
        value: int = 0

    just_above = math.nextafter(86400.0, math.inf)
    with Day.create(unique_name) as box:
        box.value = 1
        assert box.value == 1
        labels = [spec.label for spec in Day.__layout__.fields]
        with pytest.raises(ValueError, match="lock_timeout"):
            Segment.attach(unique_name, labels, Day.__layout__.schema_hash, just_above)
    with pytest.raises(ValueError, match="lock_timeout"):

        class Longer(SharedBox, lock_timeout=just_above):
            value: int = 0
