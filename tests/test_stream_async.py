import asyncio
import gc
import multiprocessing as mp
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, CancelledError, Future, wait
from multiprocessing.process import BaseProcess
from typing import Annotated

import numpy as np
import pytest

from sharedbox import (
    DType,
    Shape,
    SharedStream,
    StreamClosedError,
    StreamReader,
    StreamSender,
    WouldBlock,
)
from sharedbox._stream import WORKER_THREADS

Frame = Annotated[np.ndarray, Shape(2), DType("int32")]


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
        async with SharedStream.create(int, unique_name, capacity=4).reader() as reader:
            async with reader._stream.sender() as sender:
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


def test_an_item_taken_by_a_cancelled_anext_is_not_lost(unique_name: str) -> None:
    """Return from the next receive an item a cancelled anext had already received."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        future = reader.receive_future()
        stream.sender().send(5)
        wait([future], timeout=5)
        reader._cancel(future)
        assert reader.receive(timeout=5) == 5
        assert reader.position == 0


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


def test_cancelling_asend_leaves_the_item_unsent(unique_name: str) -> None:
    """Cancel an asend waiting on a full ring and find the ring holding only the earlier items."""

    async def run() -> list[int]:
        with SharedStream.create(int, unique_name, capacity=2) as stream:
            reader = stream.reader(start="oldest")
            sender = stream.sender()
            sender.send(0)
            sender.send(1)
            task = asyncio.create_task(sender.asend(2))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            idle = asyncio.wrap_future(sender._submit(lambda future: None))
            await asyncio.wait_for(idle, 5)
            received = [reader.receive_nowait(), reader.receive_nowait()]
            with pytest.raises(WouldBlock):
                reader.receive_nowait()
            return received

    assert asyncio.run(run()) == [0, 1]


def test_buffered_items_are_received_without_a_worker(unique_name: str) -> None:
    """Receive buffered items in order with async for and start no worker thread."""
    count = 1000

    async def run() -> tuple[list[int], float]:
        with SharedStream.create(int, unique_name, capacity=count + 1) as stream:
            reader = stream.reader(start="oldest")
            with stream.sender() as sender:
                for i in range(count):
                    sender.send(i)
            started = time.perf_counter()
            items = [item async for item in reader]
            return items, (time.perf_counter() - started) / count

    items, per_item = asyncio.run(run())
    print(f"async for over buffered items: {per_item * 1e6:.1f} us per item")
    assert items == list(range(count))
    assert not [t for t in WORKER_THREADS if unique_name in t.name]


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


def test_close_cancels_queued_futures(unique_name: str) -> None:
    """Raise StreamClosedError from a running receive_future and cancel queued ones on close."""
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
        with pytest.raises(CancelledError):
            queued.result(timeout=5)
        assert queued.cancelled()


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


def fork(target: Callable[..., object], *args: object) -> BaseProcess:
    if sys.platform == "win32":
        raise NotImplementedError("Windows has no fork")
    else:
        process = mp.get_context("fork").Process(target=target, args=args, daemon=True)
        process.start()
        return process


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
