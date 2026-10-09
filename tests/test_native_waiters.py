import contextlib
import mmap
import multiprocessing as mp
import os
import queue
import statistics
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Generator
from multiprocessing.queues import Queue
from multiprocessing.shared_memory import SharedMemory
from multiprocessing.synchronize import Event

import pytest
from os_names import os_name

from sharedbox import SharedBox
from sharedbox._layout import NativeField
from sharedbox._native import Segment, WaiterSlotsFullError

INT = 1
FIELDS = [NativeField(0, 8, INT)]
NAMES = ["a"]
SCHEMA = 0xAA17


def own_pidns() -> int:
    return 0 if sys.platform == "win32" else os.stat("/proc/self/ns/pid").st_ino


def slot_offset(slot: int, field_count: int = 1) -> int:
    """Where a waiter slot starts in the mapping: after the header, field table and write counts."""
    return 128 + field_count * 16 + slot * 24


@contextlib.contextmanager
def raw_bytes(name: str) -> Generator[memoryview, None, None]:
    if sys.platform == "win32":
        shm = SharedMemory(os_name(name))
        try:
            assert shm.buf is not None
            yield shm.buf
        finally:
            shm.close()
    else:
        with (
            open(f"/dev/shm/{os_name(name)}", "r+b") as file,
            mmap.mmap(file.fileno(), 0) as mapping,
            memoryview(mapping) as view,
        ):
            yield view


WAITERS_OFFSET = 80


def dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def create(name: str, waiter_slots: int = 64) -> Segment:
    return Segment.create(name, FIELDS, NAMES, 8, SCHEMA, 1.0, [], waiter_slots)


def attach(name: str) -> Segment:
    return Segment.attach(name, NAMES, SCHEMA, 1.0)


def hold_a_slot_until_killed(name: str, ready: Event) -> None:
    segment = attach(name)
    segment.register_waiter()
    ready.set()
    time.sleep(60)


def kill_a_slot_holder(name: str) -> None:
    context = mp.get_context("spawn")
    ready = context.Event()
    child = context.Process(target=hold_a_slot_until_killed, args=(name, ready))
    child.start()
    assert ready.wait(20)
    child.kill()
    child.join(20)


def test_register_frees_the_slot_of_a_killed_waiter(unique_name: str) -> None:
    """Check that registering a waiter reclaims the slot of a killed process and corrects the waiter count."""
    segment = create(unique_name)
    kill_a_slot_holder(unique_name)
    assert segment._waiters == 1
    slot = segment.register_waiter()
    assert segment._waiters == 1
    segment.release_waiter(slot)
    assert segment._waiters == 0
    segment.close()


def test_attach_frees_the_slot_of_a_killed_waiter(unique_name: str) -> None:
    """Check that attaching reclaims the slot of a killed process and corrects the waiter count."""
    segment = create(unique_name)
    kill_a_slot_holder(unique_name)
    assert segment._waiters == 1
    other = attach(unique_name)
    assert other._waiters == 0
    other.close()
    segment.close()


def test_every_slot_can_be_used_and_one_more_raises(unique_name: str) -> None:
    """Check that all 64 slots can be registered, the next raises WaiterSlotsFullError and releasing them empties the count."""
    segment = create(unique_name)
    slots = [segment.register_waiter() for _ in range(64)]
    assert sorted(slots) == list(range(64))
    with pytest.raises(WaiterSlotsFullError, match="all 64 waiter slots"):
        segment.register_waiter()
    for slot in slots:
        segment.release_waiter(slot)
    assert segment._waiters == 0
    segment.close()


def test_close_frees_the_slots_the_handle_holds(unique_name: str) -> None:
    """Check that closing a handle releases the waiter slots it registered."""
    segment = create(unique_name)
    other = attach(unique_name)
    other.register_waiter()
    other.register_waiter()
    assert segment._waiters == 2
    other.close()
    assert segment._waiters == 0
    segment.close()


def test_max_waiters_sets_the_slot_count(unique_name: str) -> None:
    """Check that the max_waiters class keyword sets how many slots can be registered."""

    class Few(SharedBox, max_waiters=2):
        value: int = 0

    with Few.create(unique_name) as box:
        slots = [box._segment.register_waiter() for _ in range(2)]
        with pytest.raises(WaiterSlotsFullError, match="all 2 waiter slots"):
            box._segment.register_waiter()
        for slot in slots:
            box._segment.release_waiter(slot)


@pytest.mark.parametrize("value", [0, 4097, True, 2.0])
def test_max_waiters_outside_the_range_fails_at_class_definition(value: object) -> None:
    """Check that a max_waiters value that is out of range or not an int raises ValueError at class definition."""
    with pytest.raises(ValueError, match="max_waiters"):

        class Bad(SharedBox, max_waiters=value):
            value: int = 0


@pytest.mark.skipif(
    sys.platform == "win32", reason="pid namespaces exist only on Linux"
)
def test_a_slot_of_another_pid_namespace_is_never_freed(unique_name: str) -> None:
    """Check that a slot owned by a process in another pid namespace is kept, so the next slot is used."""
    segment = create(unique_name)
    kill_a_slot_holder(unique_name)
    with raw_bytes(unique_name) as view:
        (pid,) = struct.unpack_from("<I", view, slot_offset(0) + 16)
        assert pid != 0
        struct.pack_into("<Q", view, slot_offset(0) + 8, own_pidns() + 1)
    slot = segment.register_waiter()
    assert slot == 1
    assert segment._waiters == 2
    other = attach(unique_name)
    assert other._waiters == 2
    other.close()
    segment.release_waiter(slot)
    segment.close()


def test_a_claimer_that_died_before_stamping_its_slot_is_freed_without_a_decrement(
    unique_name: str,
) -> None:
    """Check that a slot claimed but not stamped by a dead process is reused and the waiter count is adjusted once."""
    segment = create(unique_name)
    with raw_bytes(unique_name) as view:
        struct.pack_into("<QQI", view, slot_offset(0), 0, own_pidns(), dead_pid())
        (waiters,) = struct.unpack_from("<I", view, WAITERS_OFFSET)
        struct.pack_into("<I", view, WAITERS_OFFSET, waiters + 1)
    slot = segment.register_waiter()
    assert slot == 0
    assert segment._waiters == 2
    segment.release_waiter(slot)
    segment.close()


def test_release_leaves_a_slot_that_no_longer_records_this_process(
    unique_name: str,
) -> None:
    """Check that release_waiter leaves a slot alone when its recorded pid is no longer this process."""
    segment = create(unique_name)
    slot = segment.register_waiter()
    with raw_bytes(unique_name) as view:
        struct.pack_into("<I", view, slot_offset(slot) + 16, os.getpid() + 1)
    assert not segment.waiter_held(slot)
    segment.release_waiter(slot)
    assert segment._waiters == 1
    with raw_bytes(unique_name) as view:
        assert struct.unpack_from("<I", view, slot_offset(slot) + 16) == (
            os.getpid() + 1,
        )
    segment.close()


# Defined only where os.fork exists.
if sys.platform != "win32":

    @pytest.mark.filterwarnings("ignore:This process .* is multi-threaded")
    def test_a_fork_child_closing_its_handle_leaves_the_parents_slots(
        unique_name: str,
    ) -> None:
        segment = create(unique_name)
        slots = [segment.register_waiter() for _ in range(2)]
        pid = os.fork()
        if pid == 0:
            segment._after_fork()
            segment.close()
            os._exit(0)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        assert segment._waiters == 2
        assert all(segment.waiter_held(slot) for slot in slots)
        for slot in slots:
            segment.release_waiter(slot)
        segment.close()


def wait_in_child(name: str, slots: "Queue[int]", results: "Queue[float]") -> None:
    segment = attach(name)
    slot = segment.register_waiter()
    slots.put(slot)
    start = time.monotonic()
    segment.wait(segment.generation(), 5.0, slot)
    results.put(time.monotonic() - start)
    segment.close()


def test_interrupt_wakes_only_that_waiter_across_processes(unique_name: str) -> None:
    """Check that interrupting one waiter in another process wakes only that waiter."""
    segment = create(unique_name)
    context = mp.get_context("spawn")
    slots: Queue[int] = context.Queue()
    results: Queue[float] = context.Queue()
    child = context.Process(target=wait_in_child, args=(unique_name, slots, results))
    child.start()
    child_slot = slots.get(timeout=20)
    own_slot = segment.register_waiter()
    waited: list[float] = []

    def wait_here() -> None:
        start = time.monotonic()
        segment.wait(segment.generation(), 2.0, own_slot)
        waited.append(time.monotonic() - start)

    thread = threading.Thread(target=wait_here)
    thread.start()
    time.sleep(0.3)
    segment.interrupt(child_slot)
    assert results.get(timeout=20) < 1.0
    thread.join(5)
    assert waited and waited[0] >= 1.5
    child.join(20)
    segment.release_waiter(own_slot)
    segment.close()


def test_an_interrupt_sent_before_the_wait_ends_it(unique_name: str) -> None:
    """Check that an interrupt sent before wait starts makes wait return at once."""
    segment = create(unique_name)
    slot = segment.register_waiter()
    segment.interrupt(slot)
    start = time.monotonic()
    assert segment.wait(segment.generation(), 5.0, slot) == segment.generation()
    assert time.monotonic() - start < 1.0
    segment.release_waiter(slot)
    segment.close()


def test_waiting_in_a_slot_not_held_is_refused(unique_name: str) -> None:
    """Check that waiting in a slot that was not registered raises ValueError."""
    segment = create(unique_name)
    with pytest.raises(ValueError, match="not held"):
        segment.wait(segment.generation(), 0.1, 3)
    segment.close()


def test_the_longest_name_and_the_last_slot_wake(unique_name: str) -> None:
    """Check that with the longest name and 4096 slots the last slot is woken promptly by a write."""
    name = (unique_name + "x" * 128)[:128]
    segment = create(name, waiter_slots=4096)
    try:
        slots = [segment.register_waiter() for _ in range(4096)]
        last = slots[-1]
        assert last == 4095
        woke: list[tuple[int, float]] = []
        thread = threading.Thread(
            target=lambda: woke.append(
                (segment.wait(segment.generation(), 5.0, last), time.monotonic())
            )
        )
        thread.start()
        time.sleep(0.2)
        start = time.monotonic()
        segment._write([(0, bytes(8))])
        thread.join(5)
        assert [generation for generation, _ in woke] == [1]
        # A lost wake still returns the new generation, but only at the timeout.
        assert woke[0][1] - start < 1.0
        for slot in slots:
            segment.release_waiter(slot)
    finally:
        segment.close()
        Segment.unlink(name)


def test_two_waiters_wake_without_the_old_50_ms_delay(unique_name: str) -> None:
    """Check that two waiting threads wake in under 20 ms at the median after a write."""
    segment = create(unique_name)
    delays: list[float] = []

    def wait(generation: int, woke: list[float]) -> None:
        segment.wait(generation, 5.0)
        woke.append(time.monotonic())

    for _ in range(10):
        woke: list[float] = []
        threads = [
            threading.Thread(target=wait, args=(segment.generation(), woke))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        time.sleep(0.05)
        start = time.monotonic()
        segment._write([(0, bytes(8))])
        for thread in threads:
            thread.join(5)
        delays.append(max(woke) - start)
    assert statistics.median(delays) < 0.02
    segment.close()


class Counter(SharedBox):
    value: int = 0


def test_a_watcher_whose_slot_is_freed_claims_another(unique_name: str) -> None:
    """Check that a watcher whose slot was cleared claims a slot again and keeps reporting writes."""
    with Counter.create(unique_name) as box:
        seen: queue.Queue[int] = queue.Queue()
        box.events.value.connect(lambda new, old: seen.put(new))
        deadline = time.monotonic() + 5
        while box._watcher._slot is None and time.monotonic() < deadline:
            time.sleep(0.01)
        lost = box._watcher._slot
        assert lost is not None
        with raw_bytes(unique_name) as view:
            struct.pack_into("<QQI", view, slot_offset(lost), 0, 0, 0)
        # The watcher notices at its next 1 s step and claims a slot again, maybe the same one.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            slot = box._watcher._slot
            if slot is not None and box._segment.waiter_held(slot):
                with raw_bytes(unique_name) as view:
                    (owner,) = struct.unpack_from("<I", view, slot_offset(slot) + 16)
                if owner == os.getpid():
                    break
            time.sleep(0.05)
        else:
            pytest.fail("the watcher did not claim a slot again")
        box.value = 4
        assert seen.get(timeout=5) == 4


def test_a_watcher_with_every_slot_taken_still_sees_writes(unique_name: str) -> None:
    """Check that a watcher with no free slot still sees writes and claims a slot once one is freed."""

    class Single(SharedBox, max_waiters=1):
        value: int = 0

    with Single.create(unique_name) as box, Single.attach(unique_name) as other:
        taken = other._segment.register_waiter()
        values = iter(box.watch("value"))
        seen: queue.Queue[int] = queue.Queue()
        threading.Thread(target=lambda: seen.put(next(values)), daemon=True).start()
        time.sleep(0.3)
        assert box._watcher._slot is None
        other.value = 5
        assert seen.get(timeout=5) == 5
        other._segment.release_waiter(taken)
        deadline = time.monotonic() + 3
        while box._watcher._slot is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert box._watcher._slot == 0


@pytest.mark.skipif(
    sys.platform != "win32", reason="the event suffix is a Windows name"
)
def test_a_box_whose_name_ends_like_a_waiter_event_is_its_own(
    names: Callable[[str], str],
) -> None:
    """Check that box `a`'s waiter events and a box named like one of them do not collide."""
    base = names("a")
    with Counter.create(base) as box:
        seen: queue.Queue[int] = queue.Queue()
        box.events.value.connect(lambda new, old: seen.put(new))
        with Counter.create(base + "-w1") as other:
            box.value = 1
            assert seen.get(timeout=10) == 1
            assert other.value == 0
    Counter.unlink(base)
    Counter.unlink(base + "-w1")
