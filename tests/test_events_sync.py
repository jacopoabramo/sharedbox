import gc
import multiprocessing as mp
import queue
import threading
import time
from collections.abc import Iterable

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


def collect(values: Iterable[int]) -> "queue.Queue[int]":
    out: queue.Queue[int] = queue.Queue()
    threading.Thread(target=lambda: [out.put(value) for value in values], daemon=True).start()
    return out


def test_watch_sees_write_from_other_process(unique_name: str) -> None:
    with Counter.create(unique_name) as box:
        seen = collect(box.watch("value"))
        writer = mp.get_context("spawn").Process(target=set_value_later, args=(unique_name, 7, 0.2))
        writer.start()
        assert seen.get(timeout=20) == 7
        writer.join()


def test_watch_ignores_other_fields(unique_name: str) -> None:
    with Counter.create(unique_name) as box:
        seen = collect(box.watch("value"))
        box.other = 1
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)
        box.value = 2
        assert seen.get(timeout=5) == 2


def test_write_before_watch_is_not_seen(unique_name: str) -> None:
    with Counter.create(unique_name) as box:
        box.value = 1
        seen = collect(box.watch("value"))
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)


def test_watch_skips_to_the_latest_value(unique_name: str) -> None:
    with Counter.create(unique_name) as box:
        values = iter(box.watch("value"))
        box.value = 1
        box.value = 2
        assert next(values) == 2
        box.value = 3
        assert next(values) == 3


def test_two_watches_both_see_a_write(unique_name: str) -> None:
    with Counter.create(unique_name) as box:
        first, second = collect(box.watch("value")), collect(box.watch("value"))
        box.value = 5
        assert (first.get(timeout=5), second.get(timeout=5)) == (5, 5)


def test_close_ends_a_blocked_iteration(unique_name: str) -> None:
    box = Counter.create(unique_name)
    seen: list[int] = []
    finished = threading.Event()
    watch = box.watch("value")

    def consume() -> None:
        seen.extend(watch)
        finished.set()

    consumer = threading.Thread(target=consume)
    consumer.start()
    box.value = 1
    time.sleep(0.3)
    box.close()
    consumer.join(timeout=5)
    assert not consumer.is_alive()
    assert finished.is_set()
    assert seen[-1:] == [1]


def test_close_ends_iteration_between_values(unique_name: str) -> None:
    with Counter.create(unique_name) as box:
        it = iter(box.watch("value"))
        box.value = 1
        assert next(it) == 1
        box.close()
        with pytest.raises(StopIteration):
            next(it)


def test_dropping_a_watched_box_stops_its_thread(unique_name: str) -> None:
    box = Counter.create(unique_name)
    watch = box.watch("value")
    seen: queue.Queue[int] = queue.Queue()
    consumer = threading.Thread(target=lambda: [seen.put(value) for value in watch])
    consumer.start()
    box.value = 1
    assert seen.get(timeout=5) == 1
    thread_name = f"sharedbox-watch-{unique_name}"
    del box
    gc.collect()
    deadline = time.monotonic() + 5
    while (
        any(t.name == thread_name for t in threading.enumerate()) or consumer.is_alive()
    ) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not any(t.name == thread_name for t in threading.enumerate())
    assert not consumer.is_alive()


def test_unknown_field(unique_name: str) -> None:
    with Counter.create(unique_name) as box, pytest.raises(ValueError, match="missing"):
        box.watch("missing")
