import gc
import multiprocessing as mp
import queue
import threading
import time
from collections.abc import Iterable
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event

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

    def consume() -> None:
        for value in values:
            out.put(value)

    threading.Thread(target=consume, daemon=True).start()
    return out


def test_watch_sees_write_from_other_process(unique_name: str) -> None:
    """Check that a watch yields a value written by another process."""
    with Counter.create(unique_name) as box:
        seen = collect(box.watch("value"))
        writer = mp.get_context("spawn").Process(
            target=set_value_later, args=(unique_name, 7, 0.2)
        )
        writer.start()
        assert seen.get(timeout=20) == 7
        writer.join()


def test_watch_ignores_other_fields(unique_name: str) -> None:
    """Check that a watch yields nothing for writes to other fields."""
    with Counter.create(unique_name) as box:
        seen = collect(box.watch("value"))
        box.other = 1
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)
        box.value = 2
        assert seen.get(timeout=5) == 2


def test_write_before_watch_is_not_seen(unique_name: str) -> None:
    """Check that a write made before the watch starts is not yielded."""
    with Counter.create(unique_name) as box:
        box.value = 1
        seen = collect(box.watch("value"))
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)


def test_watch_skips_to_the_latest_value(unique_name: str) -> None:
    """Check that a watch yields only the latest value after several writes."""
    with Counter.create(unique_name) as box:
        values = iter(box.watch("value"))
        box.value = 1
        box.value = 2
        assert next(values) == 2
        box.value = 3
        assert next(values) == 3


def test_two_watches_both_see_a_write(unique_name: str) -> None:
    """Check that two watches on one field both yield the same write."""
    with Counter.create(unique_name) as box:
        first, second = collect(box.watch("value")), collect(box.watch("value"))
        box.value = 5
        assert (first.get(timeout=5), second.get(timeout=5)) == (5, 5)


def test_close_ends_a_blocked_iteration(unique_name: str) -> None:
    """Check that closing the box ends an iteration blocked in a consumer thread."""
    box = Counter.create(unique_name)
    seen: list[int] = []
    finished = threading.Event()
    watch = box.watch("value")

    def consume() -> None:
        seen.extend(watch)
        finished.set()

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()
    box.value = 1
    time.sleep(0.3)
    box.close()
    consumer.join(timeout=5)
    assert not consumer.is_alive()
    assert finished.is_set()
    assert seen[-1:] == [1]


def test_close_ends_iteration_between_values(unique_name: str) -> None:
    """Check that closing the box makes the next step of the iteration raise StopIteration."""
    with Counter.create(unique_name) as box:
        it = iter(box.watch("value"))
        box.value = 1
        assert next(it) == 1
        box.close()
        with pytest.raises(StopIteration):
            next(it)


def test_dropping_a_watched_box_stops_its_thread(unique_name: str) -> None:
    """Check that dropping the last reference to a watched box stops its watcher and consumer threads."""
    box = Counter.create(unique_name)
    watch = box.watch("value")
    seen: queue.Queue[int] = queue.Queue()

    def consume() -> None:
        for value in watch:
            seen.put(value)

    consumer = threading.Thread(target=consume, daemon=True)
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
    """Check that watching an unknown field raises ValueError."""
    with Counter.create(unique_name) as box, pytest.raises(ValueError, match="missing"):
        box.watch("missing")


def test_watch_never_yields_a_value_twice(unique_name: str) -> None:
    """Check that under rapid writes a watch yields strictly increasing values with no repeats."""
    with Counter.create(unique_name) as box:
        changes = box.watch("value")
        seen: list[int] = []

        def consume() -> None:
            for value in changes:
                seen.append(value)
                if len(seen) >= 200:
                    break

        thread = threading.Thread(target=consume)
        thread.start()
        for i in range(1, 5000):
            box.value = i
            if not thread.is_alive():
                break
        thread.join(10)
    assert seen
    assert seen == sorted(set(seen))


def watch_in_child(name: str, ready: "Event", out: "Queue[int]") -> None:
    box = Counter.attach(name)
    values = iter(box.watch("value"))
    ready.set()
    out.put(next(values))
    box.close()


def test_close_returns_promptly_while_the_watcher_waits(unique_name: str) -> None:
    """Check that close returns within half a second while the watcher thread is waiting."""
    box = Counter.create(unique_name)
    box.events.value.connect(lambda new: None)
    thread_name = f"sharedbox-watch-{unique_name}"
    time.sleep(0.2)
    start = time.monotonic()
    box.close()
    # Without the interrupt, close() waits out the rest of the 1 s step, about 0.8 s.
    assert time.monotonic() - start < 0.5
    assert not any(
        t.name == thread_name and t.is_alive() for t in threading.enumerate()
    )


def test_closing_one_process_box_leaves_another_process_watch_working(
    unique_name: str,
) -> None:
    """Check that closing one handle in a process does not stop a watch in another process."""
    context = mp.get_context("spawn")
    ready = context.Event()
    out: Queue[int] = context.Queue()
    with Counter.create(unique_name) as owner:
        child = context.Process(target=watch_in_child, args=(unique_name, ready, out))
        child.start()
        assert ready.wait(20)
        watcher = Counter.attach(unique_name)
        watcher.events.value.connect(lambda new: None)
        time.sleep(0.2)
        watcher.close()
        owner.value = 3
        assert out.get(timeout=20) == 3
        child.join(20)
        assert child.exitcode == 0
