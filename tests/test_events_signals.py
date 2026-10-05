import enum
import multiprocessing as mp
import queue
import struct
import threading
import time

import psygnal
import pytest

from sharedbox import SharedBox


class Counter(SharedBox):
    value: int = 0
    other: int = 0


class Colour(enum.Enum):
    RED = 1
    GREEN = 2


class Painted(SharedBox):
    colour: Colour = Colour.RED


def set_value_later(name: str, value: int, delay: float) -> None:
    time.sleep(delay)
    box = Counter.attach(name)
    box.value = value
    box.close()


def test_field_signal_fires_for_write_in_other_process(unique_name: str) -> None:
    """Check that a field signal fires with the new and old value for a write from another process."""
    seen: queue.Queue[tuple[int, int]] = queue.Queue()
    with Counter.create(unique_name) as box:
        box.events.value.connect(lambda new, old: seen.put((new, old)))
        writer = mp.get_context("spawn").Process(
            target=set_value_later, args=(unique_name, 7, 0.2)
        )
        writer.start()
        assert seen.get(timeout=20) == (7, 0)
        writer.join()


def test_callback_may_take_only_the_new_value(unique_name: str) -> None:
    """Check that a signal callback taking only the new value is called with it."""
    seen: queue.Queue[int] = queue.Queue()
    with Counter.create(unique_name) as box:
        box.events.value.connect(seen.put)
        box.value = 3
        assert seen.get(timeout=5) == 3


def test_other_field_and_same_value_do_not_fire(unique_name: str) -> None:
    """Check that writing another field or the same value does not fire a field signal."""
    seen: queue.Queue[int] = queue.Queue()
    with Counter.create(unique_name) as box:
        box.events.value.connect(seen.put)
        box.other = 1
        box.value = 0
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)


def test_group_signal_reports_any_field(unique_name: str) -> None:
    """Check that the group signal reports the field name and value of any write."""
    seen: queue.Queue[tuple[str, object]] = queue.Queue()
    with Counter.create(unique_name) as box:
        box.events.connect(lambda info: seen.put((info.signal.name, info.args[0])))
        box.other = 5
        assert seen.get(timeout=5) == ("other", 5)


def test_callback_on_main_thread(unique_name: str) -> None:
    """Check that a callback connected with thread=main runs on the main thread."""
    seen: list[str] = []
    with Counter.create(unique_name) as box:
        box.events.value.connect(
            lambda new: seen.append(threading.current_thread().name), thread="main"
        )
        # psygnal creates the main thread's queue on first use through a defaultdict; on a
        # free-threaded build the watcher thread's first emission can race that creation and
        # land in a queue emit_queued() never reads, so the queue is created here first.
        psygnal.emit_queued()
        box.value = 1
        deadline = time.monotonic() + 5
        while not seen and time.monotonic() < deadline:
            psygnal.emit_queued()
            time.sleep(0.01)
    assert seen == ["MainThread"]


def test_watcher_keeps_emitting_after_a_callback_raises(unique_name: str) -> None:
    """Check that the watcher keeps emitting to other callbacks after one callback raises."""
    seen: queue.Queue[int] = queue.Queue()

    def fail(new: int) -> None:
        raise RuntimeError("boom")

    with Counter.create(unique_name) as box:
        box.events.value.connect(seen.put)
        box.events.value.connect(fail)
        box.value = 1
        assert seen.get(timeout=5) == 1
        box.value = 2
        assert seen.get(timeout=5) == 2


def test_closing_from_a_callback_does_not_deadlock(unique_name: str) -> None:
    """Check that a callback can close its own box without deadlock."""
    box = Counter.create(unique_name)
    box.events.value.connect(lambda new: box.close())
    box.value = 1
    deadline = time.monotonic() + 5
    while not box.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert box.closed


def test_no_emission_after_close(unique_name: str) -> None:
    """Check that a closed box emits nothing for later writes by another handle."""
    seen: queue.Queue[int] = queue.Queue()
    box = Counter.create(unique_name)
    other = Counter.attach(unique_name)
    box.events.value.connect(seen.put)
    box.close()
    other.value = 4
    with pytest.raises(queue.Empty):
        seen.get(timeout=0.3)
    other.close()


def test_writes_between_two_checks_give_one_emission(unique_name: str) -> None:
    """Check that several writes made while a callback blocks the watcher give one emission with the first old value."""
    seen: queue.Queue[tuple[int, int]] = queue.Queue()
    entered, release = threading.Event(), threading.Event()

    def block(new: int) -> None:
        entered.set()
        release.wait(5)

    with Counter.create(unique_name) as box:
        box.events.other.connect(block)
        box.events.value.connect(lambda new, old: seen.put((new, old)))
        box.other = 1
        assert entered.wait(5)
        box.value = 1
        box.value = 2
        release.set()
        assert seen.get(timeout=5) == (2, 0)
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)


def test_old_is_the_last_emitted_value_after_a_same_value_write(
    unique_name: str,
) -> None:
    """Check that a write of an unchanged value emits nothing and the next change reports the last emitted value as old."""
    seen: queue.Queue[tuple[int, int]] = queue.Queue()
    with Counter.create(unique_name) as box:
        box.events.value.connect(lambda new, old: seen.put((new, old)))
        box.value = 5
        assert seen.get(timeout=5) == (5, 0)
        box.value = 5
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)
        box.value = 6
        assert seen.get(timeout=5) == (6, 5)


def test_one_update_emits_every_changed_field(unique_name: str) -> None:
    """Check that one update call emits a signal for each field it changed."""
    seen: queue.Queue[tuple[str, int, int]] = queue.Queue()
    with Counter.create(unique_name) as box:
        box.events.value.connect(lambda new, old: seen.put(("value", new, old)))
        box.events.other.connect(lambda new, old: seen.put(("other", new, old)))
        box.update(value=1, other=2)
        got = {seen.get(timeout=5), seen.get(timeout=5)}
        assert got == {("value", 1, 0), ("other", 2, 0)}


def test_a_change_that_does_not_decode_is_logged_and_skipped(
    unique_name: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Log a stored value that does not decode, skip it, and still emit the next change."""
    failed = "reading a change of field 'colour' failed"
    seen: queue.Queue[tuple[Colour, Colour]] = queue.Queue()
    with Painted.create(unique_name) as box:
        box.events.colour.connect(lambda new, old: seen.put((new, old)))
        index = Painted.__layout__.by_name["colour"].index
        box._segment._write([(index, struct.pack("<H", 9))])
        deadline = time.monotonic() + 5
        while failed not in caplog.text and time.monotonic() < deadline:
            time.sleep(0.01)
        assert failed in caplog.text
        box.colour = Colour.GREEN
        assert seen.get(timeout=5) == (Colour.GREEN, Colour.RED)
