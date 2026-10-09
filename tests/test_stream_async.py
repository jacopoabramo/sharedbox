import asyncio
import gc
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, Future, wait
from dataclasses import dataclass
from typing import Annotated, Any

import numpy as np
import pytest
from crossproc import fork

from sharedbox import (
    DType,
    Shape,
    SharedStream,
    StreamClosedError,
    StreamReader,
    StreamSender,
    WouldBlock,
    _stream,
)
from sharedbox._stream import WORKER_THREADS

Frame = Annotated[np.ndarray, Shape(2), DType("int32")]


async def worker_started(name: str) -> None:
    """Return once a background thread of the stream `name` exists, so a call went to it."""
    deadline = time.monotonic() + 5
    while not [t for t in threading.enumerate() if name in t.name]:
        assert time.monotonic() < deadline
        await asyncio.sleep(0)


class Hold:
    """An item type whose item 1, once armed, stops in its decoding until released, on whichever thread decodes it."""

    def __init__(self) -> None:
        self.armed = threading.Event()
        self.entered = threading.Event()
        self.release = threading.Event()
        hold = self

        @dataclass
        class Held:
            index: int

            def __post_init__(self) -> None:
                if hold.armed.is_set() and self.index == 1:
                    hold.armed.clear()
                    hold.entered.set()
                    hold.release.wait(5)

        self.type: type[Any] = Held

    def send_and_wait(self, sender: StreamSender[Any]) -> None:
        """Send item 1 and return once a reader's thread stopped decoding it."""
        item = self.type(1)
        self.armed.set()
        sender.send(item)
        assert self.entered.wait(5)


@pytest.fixture
def hold() -> Iterator[Hold]:
    hold = Hold()
    yield hold
    hold.release.set()


def test_receive_future_is_a_real_future(unique_name: str) -> None:
    """Return a concurrent.futures.Future that concurrent.futures.wait can wait on."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        reader = stream.reader()
        future = reader.receive_future()
        assert isinstance(future, Future)
        stream.sender().send(4)
        done, _ = wait([future], timeout=5, return_when=FIRST_COMPLETED)
        assert done == {future}
        assert future.result() == 4


def test_async_iteration_and_asend(unique_name: str) -> None:
    """Send with asend and receive with async for over the reader."""

    async def run() -> list[int]:
        stream = SharedStream.create(int, unique_name, capacity=4)
        async with stream.reader() as reader:
            async with stream.sender() as sender:
                for i in range(3):
                    await sender.asend(i)
            return [item async for item in reader]

    assert asyncio.run(run()) == [0, 1, 2]


def test_async_iteration_into_arrays(unique_name: str) -> None:
    """Receive into the same array with async for over iter_into."""

    async def run() -> list[int]:
        stream = SharedStream.create(Frame, unique_name, capacity=4)
        out = np.empty(2, np.int32)
        async with stream.reader() as reader:
            async with stream.sender() as sender:
                for i in range(3):
                    await sender.asend(np.full(2, i, np.int32))
            return [int(item[0]) async for item in reader.iter_into(out)]

    assert asyncio.run(run()) == [0, 1, 2]


def test_anext_works_with_asyncio_tasks(unique_name: str) -> None:
    """Run anext(reader) as a task and get the item sent after it started."""

    async def run() -> int:
        with SharedStream.create(int, unique_name, capacity=2) as stream:
            reader = stream.reader()
            task = asyncio.create_task(anext(reader))
            await asyncio.sleep(0.1)
            stream.sender().send(7)
            return await asyncio.wait_for(task, 5)

    assert asyncio.run(run()) == 7


def test_cancelling_anext_removes_the_wait(unique_name: str) -> None:
    """Cancel a waiting anext with asyncio.timeout and receive normally afterwards."""

    async def run() -> int:
        with SharedStream.create(int, unique_name, capacity=2) as stream:
            reader = stream.reader()
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.2):
                    await anext(reader)
            stream.sender().send(1)
            return reader.receive(timeout=5)

    assert asyncio.run(run()) == 1


def test_an_item_taken_by_a_cancelled_anext_is_not_lost(
    unique_name: str, hold: Hold
) -> None:
    """Return from the next receive an item a cancelled anext had already received."""

    async def run() -> tuple[int, int | None]:
        with SharedStream.create(hold.type, unique_name, capacity=4) as stream:
            reader = stream.reader()
            task = asyncio.create_task(anext(reader))
            await worker_started(unique_name)
            hold.send_and_wait(stream.sender())
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            hold.release.set()
            return reader.receive(timeout=5).index, reader.position

    assert asyncio.run(run()) == (1, 0)


def test_close_drops_items_kept_from_a_cancelled_receive(unique_name: str) -> None:
    """Raise StreamClosedError from receive after close although an item was kept."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        future = reader.receive_future()
        stream.sender().send(5)
        wait([future], timeout=5)
        reader._cancel(future)
        reader.close()
        with pytest.raises(StreamClosedError):
            reader.receive(timeout=1)


def test_an_asend_cancelled_while_the_ring_stays_full_sends_nothing(
    unique_name: str,
) -> None:
    """Send nothing from an asend cancelled while the ring stays full until the sender closes."""

    async def run() -> list[int]:
        with SharedStream.create(int, unique_name, capacity=2) as stream:
            reader = stream.reader(start="oldest")
            sender = stream.sender()
            sender.send(0)
            sender.send(1)
            task = asyncio.create_task(sender.asend(2))
            await worker_started(unique_name)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            sender.close()
            return list(reader)

    assert asyncio.run(run()) == [0, 1]


@pytest.mark.parametrize("every", [64, 1])
def test_buffered_items_are_received_without_a_worker(
    unique_name: str, monkeypatch: pytest.MonkeyPatch, every: int
) -> None:
    """Receive buffered items in order with async for while no worker thread exists."""
    monkeypatch.setattr(_stream, "YIELD_EVERY", every)
    count = 1000

    async def run() -> tuple[list[int], float]:
        with SharedStream.create(int, unique_name, capacity=count + 1) as stream:
            reader = stream.reader(start="oldest")
            with stream.sender() as sender:
                for i in range(count):
                    sender.send(i)
            started = time.perf_counter()
            items = [item async for item in reader]
            elapsed = (time.perf_counter() - started) / count
            assert not [t for t in _stream.WORKER_THREADS if unique_name in t.name]
            assert not [t for t in threading.enumerate() if unique_name in t.name]
            return items, elapsed

    items, per_item = asyncio.run(run())
    print(f"yield every {every}: {per_item * 1e6:.1f} us per item")
    assert items == list(range(count))


def test_a_cancelled_anext_does_not_let_the_next_one_overtake(
    unique_name: str, hold: Hold
) -> None:
    """Return the item a cancelled anext took before the one sent after it."""

    async def run() -> list[int]:
        with SharedStream.create(hold.type, unique_name, capacity=8) as stream:
            reader = stream.reader()
            sender = stream.sender()
            first = asyncio.create_task(anext(reader))
            await worker_started(unique_name)
            hold.send_and_wait(sender)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            sender.send(hold.type(2))
            second = asyncio.create_task(anext(reader))
            await asyncio.sleep(0.05)
            hold.release.set()
            received = [(await asyncio.wait_for(second, 5)).index]
            received.append((await asyncio.wait_for(anext(reader), 5)).index)
            return received

    assert asyncio.run(run()) == [1, 2]


def test_a_sync_receive_after_a_cancelled_anext_returns_its_item_first(
    unique_name: str, hold: Hold
) -> None:
    """Return the item a cancelled anext was still decoding before a later item, from every sync receive."""

    async def run() -> list[int]:
        with SharedStream.create(hold.type, unique_name, capacity=8) as stream:
            reader = stream.reader()
            sender = stream.sender()
            first = asyncio.create_task(anext(reader))
            await worker_started(unique_name)
            hold.send_and_wait(sender)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            sender.send(hold.type(2))
            with pytest.raises(WouldBlock):
                reader.receive_nowait()
            with pytest.raises(TimeoutError):
                reader.receive(timeout=0.2)
            hold.release.set()
            return [reader.receive(timeout=5).index, reader.receive(timeout=5).index]

    assert asyncio.run(run()) == [1, 2]


def test_a_second_asend_does_not_block_the_event_loop(unique_name: str) -> None:
    """Keep the event loop turning while two asend calls wait on a full ring."""

    async def run() -> float:
        with SharedStream.create(int, unique_name, capacity=2) as stream:
            reader = stream.reader(start="oldest")
            sender = stream.sender()
            sender.send(0)
            sender.send(1)
            first = asyncio.create_task(sender.asend(2))
            await asyncio.sleep(0.05)
            second = asyncio.create_task(sender.asend(3))
            loop = asyncio.get_running_loop()
            worst = 0.0
            end = loop.time() + 0.3
            last = loop.time()
            while last < end:
                await asyncio.sleep(0.01)
                now = loop.time()
                worst = max(worst, now - last)
                last = now
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)
            reader.close()
            return worst

    assert asyncio.run(run()) < 0.5


def test_a_dropped_end_lets_its_worker_stop(unique_name: str) -> None:
    """End the worker thread of a reader that completed a call and was dropped without close."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        reader = stream.reader()
        stream.sender().send(1)
        assert reader.receive_future().result(timeout=5) == 1
        threads = [t for t in WORKER_THREADS if unique_name in t.name]
        assert len(threads) == 1
        del reader
        gc.collect()
        threads[0].join(5)
        assert not threads[0].is_alive()


def test_close_ends_queued_futures_with_stream_closed_error(unique_name: str) -> None:
    """Raise StreamClosedError from both a running and a queued receive_future on close."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        reader = stream.reader()
        running = reader.receive_future()
        queued = reader.receive_future()
        deadline = time.monotonic() + 5
        while not running.running() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert running.running()
        reader.close()
        with pytest.raises(StreamClosedError):
            running.result(timeout=5)
        with pytest.raises(StreamClosedError):
            queued.result(timeout=5)
        assert not queued.cancelled()


def test_close_ends_a_queued_asend_with_stream_closed_error(unique_name: str) -> None:
    """Raise StreamClosedError in a TaskGroup from an asend queued behind a running one when the sender closes."""

    async def run() -> tuple[BaseException, ...]:
        with SharedStream.create(int, unique_name, capacity=2) as stream:
            reader = stream.reader()
            sender = stream.sender()
            sender.send(0)
            sender.send(1)
            running = asyncio.create_task(sender.asend(2))
            await worker_started(unique_name)
            with pytest.raises(ExceptionGroup) as caught:
                async with asyncio.TaskGroup() as group:
                    group.create_task(sender.asend(3))
                    await asyncio.sleep(0.05)
                    sender.close()
            with pytest.raises(StreamClosedError):
                await running
            reader.close()
            return caught.value.exceptions

    errors = asyncio.run(run())
    assert [type(error) for error in errors] == [StreamClosedError]


def test_close_ends_an_anext_queued_behind_a_cancelled_one(unique_name: str) -> None:
    """Raise StreamClosedError, not CancelledError, from an anext queued behind a cancelled one when the reader closes."""

    async def run() -> None:
        with SharedStream.create(int, unique_name, capacity=4) as stream:
            reader = stream.reader()
            first = asyncio.create_task(anext(reader))
            await worker_started(unique_name)
            second = asyncio.create_task(anext(reader))
            await asyncio.sleep(0.05)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            reader.close()
            with pytest.raises(StreamClosedError):
                await asyncio.wait_for(second, 5)

    asyncio.run(run())


def test_a_process_exits_while_anext_waits(unique_name: str) -> None:
    """Exit within a few seconds and with code 0 while a worker waits with no timeout."""
    script = textwrap.dedent(
        f"""
        from sharedbox import SharedStream
        stream = SharedStream.create(int, {unique_name!r}, capacity=2)
        future = stream.reader().receive_future()
        """
    )
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-c", script], timeout=30, check=False)
    assert done.returncode == 0
    assert time.monotonic() - started < 15


def use_inherited_ends(reader: StreamReader[int], sender: StreamSender[int]) -> None:
    """Check in a forked child that both inherited ends are closed, then close them."""
    with pytest.raises(StreamClosedError):
        reader.receive_nowait()
    with pytest.raises(StreamClosedError):
        sender.send_nowait(0)
    with pytest.raises(StreamClosedError):
        reader.receive_future()
    with pytest.raises(StreamClosedError):
        asyncio.run(anext(reader))
    reader.close()
    sender.close()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no fork")
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded")
def test_an_inherited_end_is_closed_in_the_child_and_works_in_the_parent(
    unique_name: str,
) -> None:
    """Refuse use of inherited ends in a forked child while the parent's ends keep working."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()
        sender.send(1)
        assert reader.receive_future().result(timeout=5) == 1
        future = reader.receive_future()
        sender.send(3)
        wait([future], timeout=5)
        reader._cancel(future)
        child = fork(use_inherited_ends, reader, sender)
        child.join(timeout=20)
        assert child.exitcode == 0
        assert reader.receive_future().result(timeout=5) == 3
        sender.send(2)
        assert reader.receive_future().result(timeout=5) == 2
