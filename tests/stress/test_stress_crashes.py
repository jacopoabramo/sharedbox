import multiprocessing as mp
import time
from collections.abc import Callable
from multiprocessing.synchronize import Event

import pytest
from stress_helpers import leftovers, scaled

from sharedbox import LockTimeoutError, SharedBox

pytestmark = pytest.mark.stress


class Tick(SharedBox, lock_timeout=0.2):
    value: int = 0


def hold_the_lock(name: str, ready: Event) -> None:
    box = Tick.attach(name)
    box._segment._hold_write_lock()
    ready.set()
    time.sleep(60)


def write_and_watch(name: str, ready: Event) -> None:
    box = Tick.attach(name)
    box.events.value.connect(lambda new, old: None)
    ready.set()
    i = 0
    while True:
        i += 1
        box.value = i


def test_processes_killed_mid_write_leave_nothing_behind(
    unique_name: str, report: Callable[[dict[str, object]], None]
) -> None:
    rounds = scaled(200)
    context = mp.get_context("spawn")
    forced = recovered_slots = 0
    start = time.monotonic()
    with Tick.create(unique_name) as box:
        for i in range(rounds):
            ready = context.Event()
            target = hold_the_lock if i % 2 == 0 else write_and_watch
            child = context.Process(target=target, args=(unique_name, ready))
            child.start()
            assert ready.wait(30)
            time.sleep(0.01)
            child.kill()
            child.join(30)
            try:
                box.value = -i
            except LockTimeoutError:
                box.force_unlock()
                forced += 1
                box.value = -i
            assert box.value == -i
            slot = box._segment.register_waiter()
            if box._segment._waiters == 1:
                recovered_slots += 1
            box._segment.release_waiter(slot)
        waiters_left = box._segment._waiters
    Tick.unlink(unique_name)
    report(
        {
            "rounds": rounds,
            "seconds": time.monotonic() - start,
            "forced_unlocks": forced,
            "rounds_with_every_dead_slot_freed": recovered_slots,
            "waiters_left": waiters_left,
            "shm_leftovers": leftovers(unique_name),
        }
    )
    assert forced >= rounds // 2
    assert recovered_slots == rounds
    assert waiters_left == 0
    assert leftovers(unique_name) == []
