import multiprocessing as mp
import threading
import time

from sharedbox._layout import NativeField
from sharedbox._native import Segment

INT = 1
FIELDS = [NativeField(0, 8, INT)]
NAMES = ["a"]
SCHEMA = 0xA11


def write_after(name: str, delay: float) -> None:
    time.sleep(delay)
    segment = Segment.attach(name, NAMES, SCHEMA, 1.0)
    segment._write([(0, bytes(8))])
    segment.close()


def test_wait_times_out_without_writes(unique_name: str) -> None:
    segment = Segment.create(unique_name, FIELDS, NAMES, 8, SCHEMA, 1.0, [])
    start = time.monotonic()
    assert segment.wait(0, 0.2) == 0
    assert 0.15 <= time.monotonic() - start < 2.0
    segment.close()


def test_wait_returns_at_once_when_behind(unique_name: str) -> None:
    segment = Segment.create(unique_name, FIELDS, NAMES, 8, SCHEMA, 1.0, [])
    segment._write([(0, bytes(8))])
    assert segment.wait(0, 5.0) == 1
    segment.close()


def test_wait_wakes_on_write_from_other_process(unique_name: str) -> None:
    segment = Segment.create(unique_name, FIELDS, NAMES, 8, SCHEMA, 1.0, [])
    writer = mp.get_context("spawn").Process(
        target=write_after, args=(unique_name, 0.3)
    )
    writer.start()
    start = time.monotonic()
    assert segment.wait(0, 20.0) == 1
    assert time.monotonic() - start < 10.0
    writer.join()
    segment.close()


def test_wait_wakes_every_waiter(unique_name: str) -> None:
    # Regression test for a Windows bug: a permit released for one waiter could be
    # taken by another, leaving the first asleep until its full timeout. If this
    # test hangs or times out on Windows, the wake-up delay bound has regressed.
    segment = Segment.create(unique_name, FIELDS, NAMES, 8, SCHEMA, 1.0, [])
    generation = segment.generation()
    results: list[int] = [-1] * 4

    def waiter(index: int) -> None:
        results[index] = segment.wait(generation, 20.0)

    threads = [threading.Thread(target=waiter, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    time.sleep(0.2)
    segment._write([(0, bytes(8))])
    for thread in threads:
        thread.join(timeout=2.0)
        assert not thread.is_alive()
    assert results == [generation + 1] * 4
    segment.close()


def test_wait_releases_the_gil(unique_name: str) -> None:
    segment = Segment.create(unique_name, FIELDS, NAMES, 8, SCHEMA, 1.0, [])
    ticks: list[float] = []
    done = threading.Event()

    def count() -> None:
        while not done.wait(0.01):
            ticks.append(time.monotonic())

    counter = threading.Thread(target=count)
    counter.start()
    start = time.monotonic()
    segment.wait(0, 1.0)
    done.set()
    counter.join()
    assert any(start + 0.3 <= tick <= start + 0.7 for tick in ticks)
    segment.close()
