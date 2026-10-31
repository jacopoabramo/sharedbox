import multiprocessing as mp
from multiprocessing.synchronize import Event

import pytest

from sharedbox import SegmentStatus, SharedBox, SharedStream, segment_status


class Beacon(SharedBox):
    on: bool = False


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
