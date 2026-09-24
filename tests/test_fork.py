import multiprocessing as mp
import queue
import sys
import threading
from collections.abc import Callable
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event

import pytest

from sharedbox import SharedBox

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="Windows has no fork"),
    pytest.mark.filterwarnings("ignore:This process .* is multi-threaded"),
]


class Counter(SharedBox):
    value: int = 0


def close_box(box: Counter) -> None:
    box.close()


def watch_box(box: Counter, ready: Event, out: "Queue[int]") -> None:
    values = iter(box.watch("value"))
    ready.set()
    out.put(next(values))
    box.close()


def busy_box(name: str) -> Counter:
    """A box whose watcher thread is waiting inside the segment, with events connected."""
    box = Counter.create(name)
    box.events.value.connect(lambda new: None)
    seen: queue.Queue[int] = queue.Queue()
    threading.Thread(
        target=lambda: seen.put(next(iter(box.watch("value")))), daemon=True
    ).start()
    box.value = 1
    seen.get(timeout=5)
    return box


def fork(target: Callable[..., object], *args: object) -> BaseProcess:
    if sys.platform == "win32":
        raise NotImplementedError("Windows has no fork")
    else:
        process = mp.get_context("fork").Process(target=target, args=args, daemon=True)
        process.start()
        return process


def finish(process: BaseProcess) -> int | None:
    process.join(timeout=20)
    if process.is_alive():
        process.kill()
        process.join()
        return None
    return process.exitcode


def test_forked_child_closes_an_inherited_box(unique_name: str) -> None:
    with busy_box(unique_name) as box:
        child = fork(close_box, box)
        assert finish(child) == 0


def test_forked_child_watches_an_inherited_box(unique_name: str) -> None:
    ctx = mp.get_context("fork")
    ready = ctx.Event()
    out: Queue[int] = ctx.Queue()
    with busy_box(unique_name) as box:
        child = fork(watch_box, box, ready, out)
        assert ready.wait(10)
        box.value = 9
        assert out.get(timeout=10) == 9
        assert finish(child) == 0
