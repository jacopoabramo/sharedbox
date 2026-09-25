import multiprocessing as mp
import os

from sharedbox._native import _process_alive, _process_start


def test_this_process_is_alive() -> None:
    start = _process_start(os.getpid())
    assert start != 0
    assert _process_alive(os.getpid(), start)


def test_a_different_start_time_means_another_process() -> None:
    start = _process_start(os.getpid())
    assert not _process_alive(os.getpid(), start + 1)


def test_an_exited_process_is_dead() -> None:
    child = mp.get_context("spawn").Process(target=int)
    child.start()
    assert child.pid is not None
    start = _process_start(child.pid)
    child.join()
    assert not _process_alive(child.pid, start)
