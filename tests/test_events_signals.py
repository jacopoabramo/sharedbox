import multiprocessing as mp
import queue
import threading
import time

import psygnal
import pytest

from sharedbox import SharedBox


class Counter(SharedBox):
    value: int = 0
    other: int = 0


def set_value_later(name: str, value: int, delay: float) -> None:
    time.sleep(delay)
    box = Counter.attach(name)
    box.value = value
    box.close()


def test_field_signal_fires_for_write_in_other_process(unique_name: str) -> None:
    seen: queue.Queue[tuple[int, int]] = queue.Queue()
    with Counter.create(unique_name) as box:
        box.events.value.connect(lambda new, old: seen.put((new, old)))
        writer = mp.get_context("spawn").Process(target=set_value_later, args=(unique_name, 7, 0.2))
        writer.start()
        assert seen.get(timeout=20) == (7, 0)
        writer.join()


def test_callback_may_take_only_the_new_value(unique_name: str) -> None:
    seen: queue.Queue[int] = queue.Queue()
    with Counter.create(unique_name) as box:
        box.events.value.connect(seen.put)
        box.value = 3
        assert seen.get(timeout=5) == 3


def test_other_field_and_same_value_do_not_fire(unique_name: str) -> None:
    seen: queue.Queue[int] = queue.Queue()
    with Counter.create(unique_name) as box:
        box.events.value.connect(seen.put)
        box.other = 1
        box.value = 0
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)


def test_group_signal_reports_any_field(unique_name: str) -> None:
    seen: queue.Queue[tuple[str, object]] = queue.Queue()
    with Counter.create(unique_name) as box:
        box.events.connect(lambda info: seen.put((info.signal.name, info.args[0])))
        box.other = 5
        assert seen.get(timeout=5) == ("other", 5)


def test_callback_on_main_thread(unique_name: str) -> None:
    seen: list[str] = []
    with Counter.create(unique_name) as box:
        box.events.value.connect(lambda new: seen.append(threading.current_thread().name), thread="main")
        box.value = 1
        deadline = time.monotonic() + 5
        while not seen and time.monotonic() < deadline:
            psygnal.emit_queued()
            time.sleep(0.01)
    assert seen == ["MainThread"]


def test_failing_callback_does_not_stop_emission(unique_name: str) -> None:
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
    box = Counter.create(unique_name)
    box.events.value.connect(lambda new: box.close())
    box.value = 1
    deadline = time.monotonic() + 5
    while not box.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert box.closed


def test_no_emission_after_close(unique_name: str) -> None:
    seen: queue.Queue[int] = queue.Queue()
    box = Counter.create(unique_name)
    other = Counter.attach(unique_name)
    box.events.value.connect(seen.put)
    box.close()
    other.value = 4
    with pytest.raises(queue.Empty):
        seen.get(timeout=0.3)
    other.close()
