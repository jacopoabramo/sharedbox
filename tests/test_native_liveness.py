import multiprocessing as mp
import os
import sys
import time
from multiprocessing.synchronize import Event
from pathlib import Path

import pytest

from sharedbox._native import _process_alive, _process_start


def wait_for(release: Event) -> None:
    release.wait(timeout=20)


def test_this_process_is_alive() -> None:
    start = _process_start(os.getpid())
    assert start != 0
    assert _process_alive(os.getpid(), start)


def test_a_different_start_time_means_another_process() -> None:
    start = _process_start(os.getpid())
    assert not _process_alive(os.getpid(), start + 1)


def test_an_exited_process_is_dead() -> None:
    context = mp.get_context("spawn")
    release = context.Event()
    child = context.Process(target=wait_for, args=(release,))
    child.start()
    assert child.pid is not None
    try:
        start = _process_start(child.pid)
        assert start != 0
    finally:
        release.set()
        child.join()
    assert not _process_alive(child.pid, start)


@pytest.mark.skipif(
    sys.platform != "linux", reason="zombie processes are checked through /proc"
)
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded")
def test_a_zombie_is_dead() -> None:
    if sys.platform != "linux":
        raise NotImplementedError("zombie processes are checked through /proc")
    else:
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        try:
            stat = Path(f"/proc/{pid}/stat")
            deadline = time.monotonic() + 20
            while stat.read_text().rpartition(")")[2].split()[0] != "Z":
                assert time.monotonic() < deadline, "the child never became a zombie"
                time.sleep(0.01)
            assert _process_start(pid) == 0
        finally:
            os.waitpid(pid, 0)
