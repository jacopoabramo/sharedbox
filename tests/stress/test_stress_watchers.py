import queue
import threading
import time
from collections.abc import Callable, Iterable

import pytest
from stress_helpers import percentiles, scale, watcher_threads

from sharedbox import SharedBox

pytestmark = pytest.mark.stress


class Tick(SharedBox):
    value: int = 0


def consume(values: Iterable[int], out: "queue.Queue[tuple[int, int]]") -> None:
    for value in values:
        out.put((value, time.perf_counter_ns()))


def open_watchers(
    name: str, count: int
) -> tuple[list[Tick], "queue.Queue[tuple[int, int]]"]:
    """``count`` handles on one box, each with a watch consumed by its own thread."""
    boxes = [Tick.attach(name) for _ in range(count)]
    seen: queue.Queue[tuple[int, int]] = queue.Queue()
    for box in boxes:
        threading.Thread(
            target=consume, args=(box.watch("value"), seen), daemon=True
        ).start()
    return boxes, seen


def test_many_watchers_follow_a_steady_writer(
    unique_name: str, report: Callable[[dict[str, object]], None]
) -> None:
    """Check that 64 watchers all receive the final value of a steady writer with low idle CPU."""
    watchers, rate, seconds = 64, 100, 5 * scale()
    with Tick.create(unique_name) as writer:
        boxes, seen = open_watchers(unique_name, watchers)
        time.sleep(1.5)
        idle_start = time.process_time()
        time.sleep(3)
        idle_cpu = time.process_time() - idle_start
        written: dict[int, int] = {}
        count = int(rate * seconds)
        for i in range(1, count + 1):
            written[i] = time.perf_counter_ns()
            writer.value = i
            time.sleep(1 / rate)
        final: dict[int, int] = {}
        latencies: list[int] = []
        deadline = time.monotonic() + 10
        while len(final) < watchers and time.monotonic() < deadline:
            try:
                value, when = seen.get(timeout=0.5)
            except queue.Empty:
                continue
            latencies.append(when - written[value])
            if value == count:
                final[len(final)] = when
        for box in boxes:
            box.close()
    report(
        {
            "watchers": watchers,
            "writes": count,
            "rate_hz": rate,
            "deliveries": len(latencies),
            "wake_us": percentiles(latencies),
            "idle_cpu_s_per_watcher_per_s": idle_cpu / watchers / 3,
        }
    )
    assert len(final) == watchers
    # 64 idle watchers wake once a second each; a busy loop would take whole cores.
    assert idle_cpu < 1.5


def test_more_watchers_than_slots_all_deliver(
    unique_name: str, report: Callable[[dict[str, object]], None]
) -> None:
    """Check that watchers beyond the slot count all receive a write and pick up freed slots after others close."""
    with Tick.create(unique_name) as writer:
        slots = Tick.__max_waiters__
        count = slots + 16
        boxes, seen = open_watchers(unique_name, count)
        time.sleep(2)
        assert watcher_threads(unique_name) == count
        start = time.perf_counter_ns()
        writer.value = 1
        arrivals: list[int] = []
        deadline = time.monotonic() + 10
        while len(arrivals) < count and time.monotonic() < deadline:
            try:
                value, when = seen.get(timeout=0.5)
            except queue.Empty:
                continue
            if value == 1:
                arrivals.append(when - start)
        assert watcher_threads(unique_name) == count
        for box in boxes[: count // 2]:
            box.close()
        # The watchers left claim the freed slots within one step.
        deadline = time.monotonic() + 5
        while (
            writer._segment._waiters != count - count // 2
            and time.monotonic() < deadline
        ):
            time.sleep(0.1)
        waiters_after_close = writer._segment._waiters
        for box in boxes[count // 2 :]:
            box.close()
    report(
        {
            "slots": slots,
            "watchers": count,
            "delivered": len(arrivals),
            "delivery_us": percentiles(arrivals),
            "waiters_after_closing_half": waiters_after_close,
        }
    )
    assert len(arrivals) == count
    assert max(arrivals) < 5_000_000_000
    assert waiters_after_close == count - count // 2
