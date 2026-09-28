import multiprocessing as mp
import queue
import statistics
import time
from collections.abc import Callable
from multiprocessing.synchronize import Event
from typing import NamedTuple

import pytest
from stress_helpers import percentiles, scaled, watcher_threads

from sharedbox import SharedBox
from sharedbox._native import WaiterSlotsFullError

pytestmark = pytest.mark.stress

IDLE = 2.0


class Single(SharedBox, max_waiters=1):
    value: int = 0


class Tick8(SharedBox, max_waiters=8):
    value: int = 0


class Tick64(SharedBox, max_waiters=64):
    value: int = 0


class Tick256(SharedBox, max_waiters=256):
    value: int = 0


class Wide(SharedBox, max_waiters=4096):
    value: int = 0


TickClass = type[Tick8] | type[Tick64] | type[Tick256]


def until(condition: Callable[[], bool], seconds: float) -> bool:
    """Whether ``condition`` held within ``seconds``, checked every 10 ms."""
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def test_one_waiter_slot_serves_a_second_watcher_by_polling(
    unique_name: str, report: Callable[[dict[str, object]], None]
) -> None:
    writes, rate = scaled(20), 5
    arrivals: dict[str, queue.Queue[tuple[int, int]]] = {
        "slotted": queue.Queue(),
        "polling": queue.Queue(),
    }
    with Single.create(unique_name) as writer:
        slotted = Single.attach(unique_name)
        slotted.events.value.connect(
            lambda new, old: arrivals["slotted"].put((new, time.perf_counter_ns()))
        )
        assert until(lambda: writer._segment._waiters == 1, 5)
        polling = Single.attach(unique_name)
        polling.events.value.connect(
            lambda new, old: arrivals["polling"].put((new, time.perf_counter_ns()))
        )
        time.sleep(0.5)
        threads_while_full = watcher_threads(unique_name)
        waiters_while_full = writer._segment._waiters
        with pytest.raises(WaiterSlotsFullError, match="all 1 waiter slots"):
            writer._segment.wait(writer._segment.generation(), 0.1)
        written: dict[int, int] = {}
        for i in range(1, writes + 1):
            written[i] = time.perf_counter_ns()
            writer.value = i
            time.sleep(1 / rate)
        latency: dict[str, list[int]] = {"slotted": [], "polling": []}
        final: dict[str, bool] = {}
        for key, seen in arrivals.items():
            final[key] = False
            deadline = time.monotonic() + 3
            while not final[key] and time.monotonic() < deadline:
                try:
                    value, when = seen.get(timeout=0.5)
                except queue.Empty:
                    continue
                latency[key].append(when - written[value])
                final[key] = value == writes
        slotted.close()
        # The polling watcher claims the freed slot at its next 1 s step.
        reclaimed = until(lambda: writer._segment._waiters == 1, 3)
        polling.close()
        waiters_after_close = writer._segment._waiters
        threads_after_close = watcher_threads(unique_name)
    report(
        {
            "writes": writes,
            "rate_hz": rate,
            "slotted_us": percentiles(latency["slotted"]),
            "polling_us": percentiles(latency["polling"]),
            "polling_deliveries": len(latency["polling"]),
            "waiters_while_full": waiters_while_full,
            "waiters_after_close": waiters_after_close,
        }
    )
    assert threads_while_full == 2
    assert waiters_while_full == 1
    assert final == {"slotted": True, "polling": True}
    # A woken watcher answers in milliseconds; one that polls once a second averages 500 ms.
    assert statistics.median(latency["slotted"]) < 250_000_000
    assert max(latency["polling"]) < 2_000_000_000
    assert reclaimed
    assert waiters_after_close == 0
    assert threads_after_close == 0


class Round(NamedTuple):
    registered: bool
    delivered: int
    waiters_after_close: int
    idle_cpu: float
    to_last_ns: list[int]
    each_ns: list[int]
    write_ns: list[int]
    alone_ns: list[int]


def wake_round(cls: TickClass, name: str, writes: int) -> Round:
    """One watcher in every slot of ``cls``; each write waits until every callback has seen it."""
    count = cls.__max_waiters__
    seen: queue.Queue[tuple[int, int]] = queue.Queue()
    with cls.create(name) as writer:
        alone: list[int] = []
        for i in range(scaled(1000)):
            start = time.perf_counter_ns()
            writer.value = -i
            alone.append(time.perf_counter_ns() - start)
        boxes = [cls.attach(name) for _ in range(count)]
        for box in boxes:
            box.events.value.connect(
                lambda new, old: seen.put((new, time.perf_counter_ns()))
            )
        registered = until(lambda: writer._segment._waiters == count, 30)
        idle_start = time.process_time()
        time.sleep(IDLE)
        idle_cpu = time.process_time() - idle_start
        to_last: list[int] = []
        each: list[int] = []
        write_ns: list[int] = []
        delivered = 0
        for i in range(1, writes + 1):
            start = time.perf_counter_ns()
            writer.value = i
            write_ns.append(time.perf_counter_ns() - start)
            got, last = 0, start
            deadline = time.monotonic() + 5
            while got < count and time.monotonic() < deadline:
                try:
                    value, when = seen.get(timeout=0.5)
                except queue.Empty:
                    continue
                if value == i:
                    got += 1
                    last = max(last, when)
                    each.append(when - start)
            delivered += got
            to_last.append(last - start)
        for box in boxes:
            box.close()
        waiters_after_close = writer._segment._waiters
    cls.unlink(name)
    return Round(
        registered,
        delivered,
        waiters_after_close,
        idle_cpu,
        to_last,
        each,
        write_ns,
        alone,
    )


def test_wake_latency_grows_about_linearly_with_waiters(
    unique_name: str, report: Callable[[dict[str, object]], None]
) -> None:
    writes = scaled(200)
    classes: tuple[TickClass, ...] = (Tick8, Tick64, Tick256)
    rounds = {
        cls.__max_waiters__: wake_round(
            cls, f"{unique_name}-{cls.__max_waiters__}", writes
        )
        for cls in classes
    }
    report(
        {
            str(n): {
                "writes": writes,
                "delivered": r.delivered,
                "to_last_callback_us": percentiles(r.to_last_ns),
                "each_callback_us": percentiles(r.each_ns),
                "write_us": percentiles(r.write_ns),
                "write_us_no_waiters": percentiles(r.alone_ns),
                "write_us_per_waiter": (
                    statistics.median(r.write_ns) - statistics.median(r.alone_ns)
                )
                / n
                / 1000,
                "idle_cpu_s_per_watcher_per_s": r.idle_cpu / n / IDLE,
                "waiters_after_close": r.waiters_after_close,
            }
            for n, r in rounds.items()
        }
    )
    for n, r in rounds.items():
        assert r.registered, n
        assert r.delivered == n * writes, n
        assert r.waiters_after_close == 0, n
        # An idle watcher wakes once a second; a busy loop would take a whole core.
        assert r.idle_cpu < 0.01 * n * IDLE + 0.1, n
    for small, big in ((8, 64), (64, 256)):
        grow = 2 * big / small
        wake_small = statistics.median(rounds[small].to_last_ns)
        wake_big = statistics.median(rounds[big].to_last_ns)
        assert wake_big <= grow * wake_small + 5_000_000, (small, big)
        write_small = statistics.median(rounds[small].write_ns)
        write_big = statistics.median(rounds[big].write_ns)
        assert write_big <= grow * write_small + 50_000, (small, big)


def fill_slots(names: list[str], count: int, ready: Event) -> None:
    boxes = [Wide.attach(name) for name in names]
    for box in boxes:
        for _ in range(count):
            box._segment.register_waiter()
    ready.set()
    time.sleep(600)


def test_a_table_of_4096_dead_waiters(
    unique_name: str, report: Callable[[dict[str, object]], None]
) -> None:
    processes, per_process = 16, 256
    names = [f"{unique_name}-a", f"{unique_name}-b"]
    context = mp.get_context("spawn")
    with Wide.create(names[0]) as first, Wide.create(names[1]) as second:
        start = time.monotonic()
        readies = [context.Event() for _ in range(processes)]
        children = [
            context.Process(
                target=fill_slots, args=(names, per_process, ready), daemon=True
            )
            for ready in readies
        ]
        for child in children:
            child.start()
        for ready in readies:
            assert ready.wait(600)
        fill_seconds = time.monotonic() - start
        full = (first._segment._waiters, second._segment._waiters)

        began = time.perf_counter_ns()
        with pytest.raises(WaiterSlotsFullError):
            second._segment.register_waiter()
        register_live_ns = time.perf_counter_ns() - began
        began = time.perf_counter_ns()
        live = Wide.attach(names[0])
        attach_live_ns = time.perf_counter_ns() - began
        live.close()

        for child in children:
            child.kill()
        for child in children:
            child.join(30)

        began = time.perf_counter_ns()
        attached = Wide.attach(names[0])
        attach_dead_ns = time.perf_counter_ns() - began
        waiters_after_attach = attached._segment._waiters
        attached.close()
        began = time.perf_counter_ns()
        slot = second._segment.register_waiter()
        register_dead_ns = time.perf_counter_ns() - began
        waiters_after_register = second._segment._waiters
        second._segment.release_waiter(slot)
        waiters_after_release = second._segment._waiters
    for name in names:
        Wide.unlink(name)
    report(
        {
            "processes": processes,
            "slots_per_process": per_process,
            "fill_seconds": fill_seconds,
            "register_in_full_live_table_ms": register_live_ns / 1e6,
            "attach_to_full_live_table_ms": attach_live_ns / 1e6,
            "attach_to_full_dead_table_ms": attach_dead_ns / 1e6,
            "register_in_full_dead_table_ms": register_dead_ns / 1e6,
            "waiters_after_attach": waiters_after_attach,
            "waiters_after_register": waiters_after_register,
        }
    )
    assert full == (4096, 4096)
    assert waiters_after_attach == 0
    assert waiters_after_register == 1
    assert waiters_after_release == 0
    # 4096 failed liveness checks take milliseconds; a stall per slot would take seconds.
    assert attach_dead_ns < 1_000_000_000
    assert register_dead_ns < 1_000_000_000
