import asyncio
import concurrent.futures
import multiprocessing as mp
import threading
import time
from collections.abc import Callable

import pytest

from sharedbox import BoxClosedError, FieldFuture, SharedBox, SharedStream

CONTEXT = mp.get_context("spawn")


class Counter(SharedBox):
    value: int = 0
    other: int = 0


def write_later(name: str, value: int, delay: float) -> None:
    time.sleep(delay)
    box = Counter.attach(name)
    box.value = value
    box.close()


def send_later(name: str, item: int, delay: float) -> None:
    time.sleep(delay)
    stream = SharedStream.attach(int, name)
    with stream.sender() as sender:
        sender.send(item, timeout=20)
    stream.close()


async def await_future(future: FieldFuture[int]) -> int:
    return await future


def test_wait_returns_the_box_future_when_the_box_is_written_first(
    unique_name: str, names: Callable[[str], str]
) -> None:
    """Return only the box future from wait when another process writes the field first."""
    stream_name = names("s")
    with (
        Counter.create(unique_name) as box,
        SharedStream.create(int, stream_name, capacity=4) as stream,
        stream.reader() as reader,
    ):
        stream_future = reader.receive_future()
        box_future = box.watch("value").future()
        writer = CONTEXT.Process(target=write_later, args=(unique_name, 7, 0.3))
        writer.start()
        done, pending = concurrent.futures.wait(
            [stream_future, box_future],
            timeout=20,
            return_when=concurrent.futures.FIRST_COMPLETED,
        )
        writer.join(20)
        assert (done, pending) == ({box_future}, {stream_future})
        assert box_future.result(0) == 7
        stream_future.cancel()


def test_wait_returns_the_stream_future_when_the_stream_is_sent_first(
    unique_name: str, names: Callable[[str], str]
) -> None:
    """Return only the stream future from wait when another process sends first."""
    stream_name = names("s")
    with (
        Counter.create(unique_name) as box,
        SharedStream.create(int, stream_name, capacity=4) as stream,
        stream.reader() as reader,
    ):
        stream_future = reader.receive_future()
        box_future = box.watch("value").future()
        sender = CONTEXT.Process(target=send_later, args=(stream_name, 5, 0.3))
        sender.start()
        done, pending = concurrent.futures.wait(
            [stream_future, box_future],
            timeout=20,
            return_when=concurrent.futures.FIRST_COMPLETED,
        )
        sender.join(20)
        assert (done, pending) == ({stream_future}, {box_future})
        assert stream_future.result(0) == 5


def test_as_completed_yields_field_futures_in_write_order(unique_name: str) -> None:
    """Yield the futures of two fields in the order they were written."""
    with Counter.create(unique_name) as box:
        first = box.watch("value").future()
        second = box.watch("other").future()
        box.other = 2
        done = concurrent.futures.as_completed([first, second], timeout=10)
        assert next(done) is second
        box.value = 1
        assert next(done) is first
        assert (first.result(0), second.result(0)) == (1, 2)


def test_wrap_future_and_await_resolve_in_a_loop(unique_name: str) -> None:
    """Resolve both asyncio.wrap_future and a direct await with the written value."""

    async def main() -> tuple[int, int]:
        with Counter.create(unique_name) as box:
            watch = box.watch("value")
            wrapped = asyncio.wrap_future(watch.future())
            awaited = asyncio.ensure_future(await_future(watch.future()))
            box.value = 4
            return (
                await asyncio.wait_for(wrapped, 10),
                await asyncio.wait_for(awaited, 10),
            )

    assert asyncio.run(main()) == (4, 4)


def test_cancelling_an_awaiting_task_cancels_the_future(unique_name: str) -> None:
    """Cancel the future when the task awaiting it is cancelled."""

    async def main() -> bool:
        with Counter.create(unique_name) as box:
            future = box.watch("value").future()
            task = asyncio.ensure_future(await_future(future))
            await asyncio.sleep(0.1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return future.cancelled()

    assert asyncio.run(main())


def test_cancel_before_a_write_keeps_the_future_cancelled(unique_name: str) -> None:
    """Report the future cancelled and never resolve it after a later write."""
    with Counter.create(unique_name) as box:
        future = box.watch("value").future()
        assert future.cancel()
        box.value = 1
        time.sleep(0.3)
        assert future.cancelled()
        with pytest.raises(concurrent.futures.CancelledError):
            future.result(0)
        done, _ = concurrent.futures.wait([future], timeout=5)
        assert done == {future}


def test_cancel_after_a_write_returns_false_and_keeps_the_result(
    unique_name: str,
) -> None:
    """Refuse to cancel a resolved future and keep its value."""
    with Counter.create(unique_name) as box:
        future = box.watch("value").future()
        box.value = 3
        assert future.result(10) == 3
        assert not future.cancel()
        assert future.result(0) == 3


def test_result_times_out(unique_name: str) -> None:
    """Raise TimeoutError from result when no write arrives in time."""
    with Counter.create(unique_name) as box:
        future = box.watch("value").future()
        with pytest.raises(TimeoutError):
            future.result(timeout=0.1)


def test_done_callback_runs_once_whether_added_before_or_after(
    unique_name: str,
) -> None:
    """Run a done callback exactly once when added before or after the write."""
    with Counter.create(unique_name) as box:
        future = box.watch("value").future()
        calls: list[str] = []
        called = threading.Event()

        def before(fut: concurrent.futures.Future[int]) -> None:
            calls.append("before")
            called.set()

        future.add_done_callback(before)
        box.value = 1
        assert called.wait(10)
        future.add_done_callback(lambda fut: calls.append("after"))
        assert calls == ["before", "after"]


def test_a_write_between_watch_and_future_resolves_at_once(unique_name: str) -> None:
    """Resolve the future immediately when the field was written after watch()."""
    with Counter.create(unique_name) as box:
        watch = box.watch("value")
        box.value = 9
        future = watch.future()
        assert future.done()
        assert future.result(0) == 9


def test_each_call_starts_from_the_watch_version(unique_name: str) -> None:
    """Resolve a second future at once when the first one saw a write after the watch."""
    with Counter.create(unique_name) as box:
        watch = box.watch("value")
        first = watch.future()
        box.value = 1
        first.result(10)
        assert watch.future().result(0) == 1


def test_version_follows_the_write(unique_name: str) -> None:
    """Report a higher version once the future has its result."""
    with Counter.create(unique_name) as box:
        future = box.watch("value").future()
        before = future.version
        box.value = 1
        future.result(10)
        assert future.version > before


def test_closing_the_box_cancels_a_pending_future(unique_name: str) -> None:
    """Cancel a pending future when the box is closed, and refuse new ones."""
    box = Counter.create(unique_name)
    watch = box.watch("value")
    future = watch.future()
    box.close()
    assert future.cancelled()
    with pytest.raises(BoxClosedError):
        watch.future()
