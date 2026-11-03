import asyncio
import contextlib
import threading
from collections.abc import Callable, Coroutine, Generator
from typing import Any, TypeVar

import pytest
from crossproc import call_in_child, serve_once

from sharedbox import Call, CallError, CallServer, SharedBox, call

T = TypeVar("T")


class Scanner(SharedBox):
    position: float = 0.0
    move: Call[[float], float] = call()
    home: Call[[], None] = call()


@contextlib.contextmanager
def running_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Run an event loop on a thread of its own for the duration of the block."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)
        loop.close()


def on_loop(loop: asyncio.AbstractEventLoop, coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run_coroutine_threadsafe(coroutine, loop).result(10)


async def serve(call: Call[Any, Any], handler: Callable[..., Any]) -> CallServer:
    return call.serve(handler)


async def double(position: float) -> float:
    return position * 2


def test_an_async_handler_can_gather_tasks(names: Callable[[str], str]) -> None:
    """Answer with the sum of two tasks an async handler gathers."""

    async def handler(position: float) -> float:
        first = asyncio.create_task(double(position))
        second = asyncio.create_task(double(position + 1))
        return sum(await asyncio.gather(first, second))

    with running_loop() as loop, Scanner.create(names("gather")) as box:
        on_loop(loop, serve(box.move, handler))
        assert box.move(1.0).result(timeout=10) == 6.0


def test_a_task_started_by_a_handler_outlives_it(names: Callable[[str], str]) -> None:
    """Keep running a task a handler started after the handler answered."""
    release = asyncio.Event()
    finished = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    async def later() -> None:
        await release.wait()
        finished.set()

    async def handler(position: float) -> float:
        tasks.append(asyncio.create_task(later()))
        return position

    async def task_running() -> bool:
        return not tasks[0].done()

    async def task_finished() -> None:
        await asyncio.wait_for(finished.wait(), 10)

    with running_loop() as loop, Scanner.create(names("outlives")) as box:
        on_loop(loop, serve(box.move, handler))
        assert box.move(1.0).result(timeout=10) == 1.0
        assert box.move(2.0).result(timeout=10) == 2.0
        assert on_loop(loop, task_running())
        loop.call_soon_threadsafe(release.set)
        on_loop(loop, task_finished())


def test_cancelled_loop_tasks_give_the_caller_call_error(
    names: Callable[[str], str],
) -> None:
    """Fail a request whose handler the loop's shutdown cancelled, and free the field."""
    serving = threading.Event()

    with Scanner.create(names("cancelled")) as box:

        async def main() -> None:
            arrived = asyncio.Event()

            async def handler(position: float) -> float:
                arrived.set()
                await asyncio.sleep(60)
                return position

            box.move.serve(handler)
            serving.set()
            await arrived.wait()

        thread = threading.Thread(target=asyncio.run, args=(main(),))
        thread.start()
        assert serving.wait(10)
        pending = box.move(1.0)
        thread.join(10)
        with pytest.raises(CallError, match="the handler was cancelled"):
            pending.result(timeout=10)
        assert call_in_child(serve_once, box, "move") is True


def test_a_handler_can_await_another_call_on_the_same_box(
    names: Callable[[str], str],
) -> None:
    """Answer a handler that awaits a call to another field served on the same loop."""
    with running_loop() as loop, Scanner.create(names("nested")) as box:

        async def home() -> None:
            assert await box.move(0.0) == 0.0

        on_loop(loop, serve(box.move, double))
        on_loop(loop, serve(box.home, home))
        assert box.home().result(timeout=10) is None


def test_an_async_handler_needs_a_running_loop(names: Callable[[str], str]) -> None:
    """Refuse to serve an async handler outside a running event loop."""
    with Scanner.create(names("noloop")) as box:
        with pytest.raises(RuntimeError, match="running event loop"):
            box.move.serve(double)
        box.move.serve(lambda position: position)


async def serve_idle(box: Scanner) -> None:
    box.move.serve(double)


def test_an_idle_async_server_frees_the_field_when_its_loop_ends(
    names: Callable[[str], str],
) -> None:
    """Free the field of an async server whose event loop ended while it was idle."""
    with Scanner.create(names("idle")) as box:
        asyncio.run(serve_idle(box))
        asyncio.run(serve_idle(box))
        # A write wakes the watcher, which frees the field of a server whose loop closed.
        box.position = 1.0
        assert call_in_child(serve_once, box, "move") is True
        box.move.serve(lambda position: position)
        assert box.move(1.0).result(timeout=10) == 1.0


def test_a_cancelled_error_from_the_handler_fails_only_that_call(
    names: Callable[[str], str],
) -> None:
    """Fail a call whose handler raised CancelledError itself, and answer the next."""

    async def handler(position: float) -> float:
        if position < 0:
            inner = asyncio.create_task(asyncio.sleep(60))
            await asyncio.sleep(0)
            inner.cancel()
            await inner
        return position

    with running_loop() as loop, Scanner.create(names("inner")) as box:
        on_loop(loop, serve(box.move, handler))
        with pytest.raises(CallError, match="CancelledError"):
            box.move(-1.0).result(timeout=10)
        assert box.move(1.0).result(timeout=10) == 1.0
