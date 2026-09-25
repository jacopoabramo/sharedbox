import errno
import multiprocessing as mp
import os
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


NOBODY = 65534
ROOT = sys.platform != "win32" and os.geteuid() == 0


class Counter(SharedBox):
    value: int = 0


def close_box(box: Counter) -> None:
    box.close()


def watch_box(box: Counter, ready: Event, out: "Queue[int]") -> None:
    values = iter(box.watch("value"))
    ready.set()
    out.put(next(values))
    box.close()


def unlink_as_another_user(name: str, out: "Queue[object]") -> None:
    if sys.platform != "win32":
        os.setuid(NOBODY)
    try:
        Counter.unlink(name)
    except OSError as error:
        out.put((type(error).__name__, error.errno))
    else:
        out.put(None)


def busy_box(name: str) -> Counter:
    """A box whose watcher thread is waiting inside the segment, with events connected."""
    box = Counter.create(name)
    box.events.value.connect(lambda new: None)
    changes = iter(box.watch("value"))
    seen: queue.Queue[int] = queue.Queue()
    threading.Thread(target=lambda: seen.put(next(changes)), daemon=True).start()
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


@pytest.mark.skipif(not ROOT, reason="needs root to switch to another user")
def test_unlink_reports_a_refused_unlink_as_oserror(unique_name: str) -> None:
    out: Queue[object] = mp.get_context("fork").Queue()
    with Counter.create(unique_name):
        child = fork(unlink_as_another_user, unique_name, out)
        assert out.get(timeout=10) in [
            ("PermissionError", errno.EACCES),
            ("PermissionError", errno.EPERM),
        ]
        assert finish(child) == 0
    Counter.unlink(unique_name)
