import contextlib
import mmap
import multiprocessing as mp
import os
import sys
import threading
import time
from collections.abc import Generator
from multiprocessing.synchronize import Event

import pytest
from forged_open import raw_bytes
from os_names import os_name

from sharedbox import (
    SchemaMismatchError,
    SegmentStatus,
    SharedBox,
    SharedStream,
    segment_status,
)


class Beacon(SharedBox):
    on: bool = False


@contextlib.contextmanager
def unpublished(name: str) -> Generator[None, None, None]:
    """A zeroed page under the segment's object name, which never gets a magic."""
    if sys.platform == "win32":
        with mmap.mmap(-1, 4096, tagname=os_name(name)):
            yield
    else:
        fd = os.open(f"/dev/shm/{os_name(name)}", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.ftruncate(fd, 4096)
            yield
        finally:
            os.close(fd)


def create_and_wait(name: str, created: Event, attached: Event) -> None:
    box = Beacon.create(name)
    created.set()
    attached.wait(10)
    box.close()


def test_status_follows_the_creator(unique_name: str) -> None:
    """Report missing before create, running while the creator runs and creator_exited after it ends."""
    assert segment_status(unique_name) is SegmentStatus.MISSING
    context = mp.get_context("spawn")
    created, attached = context.Event(), context.Event()
    child = context.Process(
        target=create_and_wait, args=(unique_name, created, attached)
    )
    child.start()
    assert created.wait(30)
    with Beacon.attach(unique_name):
        assert segment_status(unique_name) is SegmentStatus.RUNNING
        attached.set()
        child.join(30)
        assert segment_status(unique_name) is SegmentStatus.CREATOR_EXITED


def test_status_of_a_stream_this_process_created(unique_name: str) -> None:
    """Report running for a stream whose creator is this process."""
    with SharedStream.create(int, unique_name, capacity=4):
        assert segment_status(unique_name) == "running"


@pytest.mark.parametrize("name", ["", "a.b", "x" * 241])
def test_a_name_sharedbox_refuses_raises(name: str) -> None:
    """Raise ValueError for a name that is not segments of [A-Za-z0-9_-] joined by ':'."""
    with pytest.raises(ValueError):
        segment_status(name)


def test_the_wait_lets_other_threads_run(unique_name: str) -> None:
    """Let other Python threads run while waiting on a segment that is never published."""
    ticks = 0
    done = threading.Event()

    def tick() -> None:
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            time.sleep(0.01)

    ticker = threading.Thread(target=tick)
    with unpublished(unique_name):
        ticker.start()
        try:
            assert segment_status(unique_name) is SegmentStatus.MISSING
        finally:
            done.set()
            ticker.join()
    assert ticks >= 20


def test_a_core_version_this_sharedbox_cannot_read_names_no_kind(
    unique_name: str,
) -> None:
    """Raise SchemaMismatchError naming the segment and its core version, not a box layout, for a stream."""
    with (
        SharedStream.create(int, unique_name, capacity=4),
        raw_bytes(unique_name) as view,
    ):
        view[8:10] = (2).to_bytes(2, "little")
        try:
            with pytest.raises(SchemaMismatchError) as excinfo:
                segment_status(unique_name)
        finally:
            view[8:10] = (1).to_bytes(2, "little")
    message = str(excinfo.value)
    assert f"segment '{unique_name}' uses core version 2.0" in message
    assert "layout" not in message

