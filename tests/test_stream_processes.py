import asyncio
import itertools
import multiprocessing as mp
import threading
import time
from multiprocessing.queues import Queue
from typing import get_args

import numpy as np
import pytest
from streamproc import (
    Frame,
    ReaderKind,
    SenderKind,
    finish,
    frame,
    hold_sender_then_die,
    hold_sender_until,
    read_frames,
    read_ints_as,
    read_then_die,
    send_frames,
    send_ints_as,
)

from sharedbox import EndOfStream, SharedStream, StreamBusyError

CONTEXT = mp.get_context("spawn")


def test_one_sender_and_a_reader_per_mode_in_other_processes(unique_name: str) -> None:
    """Deliver every item in order to the lossless reader and never a torn image to any reader."""
    modes = ("lossless", "lossy", "latest")
    count = 2000
    with SharedStream.create(Frame, unique_name, capacity=8, max_readers=3):
        barrier = CONTEXT.Barrier(len(modes) + 1)
        results: Queue[tuple[str, list[int], int, int]] = CONTEXT.Queue()
        readers = [
            CONTEXT.Process(
                target=read_frames, args=(unique_name, mode, results, barrier)
            )
            for mode in modes
        ]
        sender = CONTEXT.Process(target=send_frames, args=(unique_name, count, barrier))
        for child in (*readers, sender):
            child.start()
        got = {
            mode: (seen, missed, torn)
            for mode, seen, missed, torn in (results.get(timeout=60) for _ in modes)
        }
        finish(sender, *readers)
    seen, missed, _ = got["lossless"]
    assert seen == list(range(count)) and missed == 0
    for mode in ("lossy", "latest"):
        seen, missed, _ = got[mode]
        assert seen == sorted(set(seen))
        assert seen[-1] == count - 1
        assert len(seen) + missed == count
    assert [torn for _, _, torn in got.values()] == [0, 0, 0]


def test_receive_into_fills_the_callers_array_across_processes(
    unique_name: str,
) -> None:
    """Return items whose image shares memory with the array passed, with the sent content."""
    with SharedStream.create(Frame, unique_name, capacity=4) as stream:
        reader = stream.reader()
        child = CONTEXT.Process(
            target=send_frames, args=(unique_name, 3, CONTEXT.Barrier(1))
        )
        child.start()
        buf = np.zeros((64, 64), np.uint16)
        for i in range(3):
            item = reader.receive_into({"image": buf}, timeout=60)
            assert np.shares_memory(item.image, buf)
            assert item.index == i
            assert (item.image == i).all()
        finish(child)


def test_readers_drain_then_end_when_the_sender_dies(unique_name: str) -> None:
    """Receive what a killed sender published, then EndOfStream within a few seconds."""
    with SharedStream.create(Frame, unique_name, capacity=4) as stream:
        reader = stream.reader()
        ready = CONTEXT.Event()
        child = CONTEXT.Process(target=hold_sender_then_die, args=(unique_name, ready))
        child.start()
        assert ready.wait(60)
        finish(child)
        assert reader.receive(timeout=10).index == 0
        start = time.monotonic()
        with pytest.raises(EndOfStream):
            reader.receive(timeout=10)
        assert time.monotonic() - start < 5


def test_a_dead_lossless_reader_stops_holding_the_sender(unique_name: str) -> None:
    """Let send continue within a few seconds after a lossless reader's process died."""
    with SharedStream.create(Frame, unique_name, capacity=2) as stream:
        ready = CONTEXT.Event()
        child = CONTEXT.Process(target=read_then_die, args=(unique_name, ready))
        child.start()
        assert ready.wait(60)
        finish(child)
        sender = stream.sender()
        sender.send(frame(0), timeout=10)
        sender.send(frame(1), timeout=10)
        start = time.monotonic()
        sender.send(frame(2), timeout=10)
        assert time.monotonic() - start < 5


def test_a_sender_of_a_dead_process_is_replaced(unique_name: str) -> None:
    """Raise StreamBusyError while the first sender lives and succeed once its process died, with no reader waiting."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        ready, release = CONTEXT.Event(), CONTEXT.Event()
        child = CONTEXT.Process(
            target=hold_sender_until, args=(unique_name, ready, release)
        )
        child.start()
        assert ready.wait(60)
        with pytest.raises(StreamBusyError):
            stream.sender()
        release.set()
        finish(child)
        deadline = time.monotonic() + 5
        while True:
            try:
                sender = stream.sender()
                break
            except StreamBusyError:
                assert time.monotonic() < deadline
                time.sleep(0.05)
        reader = stream.reader()
        sender.send(7)
        assert reader.receive(timeout=5) == 7


@pytest.mark.parametrize(
    ("sending", "receiving"),
    list(itertools.product(get_args(SenderKind), get_args(ReaderKind))),
)
def test_every_sender_and_reader_style_agrees_across_processes(
    unique_name: str, sending: SenderKind, receiving: ReaderKind
) -> None:
    """Receive every integer once and in order, whichever way the sender sends and the reader receives, across a ring that fills up."""
    capacity, count = 4, 500
    with SharedStream.create(int, unique_name, capacity=capacity) as stream:
        opened, go, start = CONTEXT.Event(), CONTEXT.Event(), CONTEXT.Event()
        results: Queue[list[int] | str] = CONTEXT.Queue()
        reader = CONTEXT.Process(
            target=read_ints_as, args=(unique_name, receiving, opened, go, results)
        )
        sender = CONTEXT.Process(
            target=send_ints_as, args=(unique_name, sending, count, start)
        )
        reader.start()
        sender.start()
        assert opened.wait(60)
        start.set()
        deadline = time.monotonic() + 60
        while stream.statistics().sent < capacity:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        go.set()
        assert results.get(timeout=60) == list(range(count))
        finish(sender, reader)


def test_send_and_asend_from_two_threads_lose_nothing(unique_name: str) -> None:
    """Deliver every item of a thread calling send and a thread calling asend on one sender, each once and in its thread's order."""
    count = 300
    with SharedStream.create(tuple[int, int], unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()

        def send_sync() -> None:
            for i in range(count):
                sender.send((0, i), timeout=60)

        def send_async() -> None:
            async def run() -> None:
                for i in range(count):
                    await sender.asend((1, i))

            asyncio.run(run())

        threads = [
            threading.Thread(target=send_sync, daemon=True),
            threading.Thread(target=send_async, daemon=True),
        ]
        for thread in threads:
            thread.start()
        got = [reader.receive(timeout=60) for _ in range(2 * count)]
        for thread in threads:
            thread.join(60)
        assert not any(thread.is_alive() for thread in threads)
    for which in (0, 1):
        assert [i for w, i in got if w == which] == list(range(count))


def test_two_event_loops_in_two_threads_each_receive_everything(
    unique_name: str,
) -> None:
    """Give each of two readers, awaited by event loops in two threads, every item of the stream."""
    count = 200
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        opened = threading.Barrier(3)
        got: dict[int, list[int]] = {}

        def consume(which: int) -> None:
            reader = stream.reader()
            opened.wait(60)

            async def run() -> None:
                got[which] = [item async for item in reader]

            asyncio.run(run())

        threads = [
            threading.Thread(target=consume, args=(n,), daemon=True) for n in (0, 1)
        ]
        for thread in threads:
            thread.start()
        opened.wait(60)
        with stream.sender() as sender:
            for i in range(count):
                sender.send(i, timeout=60)
        for thread in threads:
            thread.join(60)
        assert not any(thread.is_alive() for thread in threads)
    assert got == {0: list(range(count)), 1: list(range(count))}
