import contextlib
import mmap
import multiprocessing as mp
import os
import struct
import sys
import time
from collections.abc import Generator
from multiprocessing.shared_memory import SharedMemory
from multiprocessing.synchronize import Event

import pytest

from sharedbox import SharedBox
from sharedbox._layout import NativeField
from sharedbox._native import Segment

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
        shm = SharedMemory(f"sharedbox.{name}")
        try:
            assert shm.buf is not None
            yield shm.buf
        finally:
            shm.close()
    else:
        with (
            open(f"/dev/shm/sharedbox.{name}", "r+b") as file,
            mmap.mmap(file.fileno(), 0) as mapping,
            memoryview(mapping) as view,
        ):
            yield view


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
    segment = create(unique_name)
    kill_a_slot_holder(unique_name)
    assert segment._waiters == 1
    slot = segment.register_waiter()
    assert segment._waiters == 1
    segment.release_waiter(slot)
    assert segment._waiters == 0
    segment.close()


def test_attach_frees_the_slot_of_a_killed_waiter(unique_name: str) -> None:
    segment = create(unique_name)
    kill_a_slot_holder(unique_name)
    assert segment._waiters == 1
    other = attach(unique_name)
    assert other._waiters == 0
    other.close()
    segment.close()


def test_every_slot_can_be_used_and_one_more_raises(unique_name: str) -> None:
    segment = create(unique_name)
    slots = [segment.register_waiter() for _ in range(64)]
    assert sorted(slots) == list(range(64))
    with pytest.raises(RuntimeError, match="all 64 waiter slots"):
        segment.register_waiter()
    for slot in slots:
        segment.release_waiter(slot)
    assert segment._waiters == 0
    segment.close()


def test_close_frees_the_slots_the_handle_holds(unique_name: str) -> None:
    segment = create(unique_name)
    other = attach(unique_name)
    other.register_waiter()
    other.register_waiter()
    assert segment._waiters == 2
    other.close()
    assert segment._waiters == 0
    segment.close()


def test_max_waiters_sets_the_slot_count(unique_name: str) -> None:
    class Few(SharedBox, max_waiters=2):
        value: int = 0

    with Few.create(unique_name) as box:
        slots = [box._segment.register_waiter() for _ in range(2)]
        with pytest.raises(RuntimeError, match="all 2 waiter slots"):
            box._segment.register_waiter()
        for slot in slots:
            box._segment.release_waiter(slot)


@pytest.mark.parametrize("value", [0, 4097, True, 2.0])
def test_max_waiters_outside_the_range_fails_at_class_definition(value: object) -> None:
    with pytest.raises(ValueError, match="max_waiters"):

        class Bad(SharedBox, max_waiters=value):
            value: int = 0


@pytest.mark.skipif(
    sys.platform == "win32", reason="pid namespaces exist only on Linux"
)
def test_a_slot_of_another_pid_namespace_is_never_freed(unique_name: str) -> None:
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
