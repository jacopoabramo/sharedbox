import asyncio
import gc
import logging
import multiprocessing as mp
import sys
import time
from collections.abc import Callable
from concurrent.futures import CancelledError
from functools import partial
from multiprocessing.sharedctypes import Synchronized
from multiprocessing.synchronize import Event as EventType

import pytest
from crossproc import call_in_child, fork, serve_once, serving_in_child

from sharedbox import (
    BoxClosedError,
    Call,
    CallError,
    CallServedError,
    CallSupersededError,
    ServerExitedError,
    SharedBox,
    field,
)


class Scanner(SharedBox):
    position: float = 0.0
    move: Call[[float], float] = field(init=False)
    home: Call[[], None] = field(init=False)


def double(position: float) -> float:
    return position * 2


def stay() -> None:
    pass


def gated_double(started: EventType, release: EventType, position: float) -> float:
    started.set()
    release.wait(30)
    return position * 2


def slow_double(started: EventType, position: float) -> float:
    started.set()
    time.sleep(60)
    return position * 2


def too_far(position: float) -> float:
    raise ValueError("too far")


def long_message(position: float) -> float:
    raise ValueError("é" * 300)


def counted_double(count: "Synchronized[int]", position: float) -> float:
    with count.get_lock():
        count.value += 1
    return position * 2


def ask_from_fork(box: Scanner, count: "Synchronized[int]") -> None:
    if box.move(2.0).result(timeout=10) != 4.0 or count.value != 1:
        sys.exit(1)


async def ask(box: Scanner, position: float) -> float:
    return await box.move(position)


def test_a_call_across_processes_is_answered_and_superseded(
    names: Callable[[str], str],
) -> None:
    """Answer awaited, blocking and kept calls, and supersede a request sent while another runs."""
    with Scanner.create(names("answered")) as box:
        with serving_in_child(box, move=double, home=stay):
            assert asyncio.run(ask(box, 2.0)) == 4.0
            assert box.move(3.0).result(timeout=10) == 6.0
            kept = box.move(4.0)
            assert box.home().result(timeout=10) is None
            assert kept.result(timeout=10) == 8.0
        context = mp.get_context("spawn")
        started = context.Event()
        release = context.Event()
        with serving_in_child(box, move=partial(gated_double, started, release)):
            first = box.move(1.0)
            assert started.wait(30)
            second = box.move(2.0)
            third = box.move(3.0)
            release.set()
            assert first.result(timeout=10) == 2.0
            with pytest.raises(CallSupersededError):
                second.result(timeout=10)
            assert third.result(timeout=10) == 6.0


def test_a_killed_server_gives_server_exited_error(
    names: Callable[[str], str],
) -> None:
    """Raise ServerExitedError for a request whose server was killed while handling it."""
    started = mp.get_context("spawn").Event()
    with (
        Scanner.create(names("killed")) as box,
        serving_in_child(box, move=partial(slow_double, started)) as child,
    ):
        pending = box.move(1.0)
        assert started.wait(30)
        child.kill()
        child.join(10)
        with pytest.raises(ServerExitedError):
            pending.result(timeout=10)


def test_a_second_serve_is_refused(names: Callable[[str], str]) -> None:
    """Refuse a second serve in the same process and a serve while another process serves."""
    with Scanner.create(names("twice")) as box:
        server = box.move.serve(double)
        with pytest.raises(CallServedError):
            box.move.serve(double)
        server.stop()
        with serving_in_child(box, move=double), pytest.raises(CallServedError):
            box.move.serve(double)


def test_closing_a_serving_box_lets_another_process_serve(
    names: Callable[[str], str],
) -> None:
    """Let another process serve once the serving box is closed or garbage collected."""
    name = names("closing")
    with Scanner.create(name) as keeper:
        box = Scanner.attach(name)
        box.move.serve(double)
        box.close()
        assert call_in_child(serve_once, keeper, "move") is True
        box = Scanner.attach(name)
        server = box.move.serve(double)
        del box
        gc.collect()
        assert call_in_child(serve_once, keeper, "move") is True
        server.stop()


def test_a_handler_exception_reaches_the_caller(
    names: Callable[[str], str], caplog: pytest.LogCaptureFixture
) -> None:
    """Give the caller a CallError with the handler's error, cut to 256 bytes, and log it."""
    with Scanner.create(names("raises")) as box, caplog.at_level(logging.ERROR):
        server = box.move.serve(too_far)
        with pytest.raises(CallError) as raised:
            box.move(1.0).result(timeout=10)
        assert str(raised.value).endswith("ValueError: too far")
        server.stop()
        box.move.serve(long_message)
        with pytest.raises(CallError) as raised:
            box.move(1.0).result(timeout=10)
        text = str(raised.value).removeprefix("Scanner.move: ")
        assert text.startswith("ValueError: é")
        assert len(text.encode()) <= 256
    logged = [r for r in caplog.records if r.name == "sharedbox" and r.exc_info]
    assert len(logged) == 2


def test_a_request_sent_before_serve_is_answered(names: Callable[[str], str]) -> None:
    """Answer a request that was sent before anyone served the field."""
    with Scanner.create(names("early")) as box:
        pending = box.move(1.5)
        box.move.serve(double)
        assert pending.result(timeout=10) == 3.0


def test_a_call_refuses_keywords_and_wrong_arguments(
    names: Callable[[str], str],
) -> None:
    """Raise TypeError for keyword and mistyped arguments without sending a request."""
    with Scanner.create(names("arguments")) as box:
        box.move.serve(double)
        with pytest.raises(TypeError, match="positional arguments only"):
            box.move(position=1.0)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            box.move("x")  # type: ignore[arg-type]
        assert box.move(1.0).result(timeout=10) == 2.0


def test_closing_the_box_cancels_pending_calls(names: Callable[[str], str]) -> None:
    """Cancel a pending call when the box closes, and refuse a call on a closed box."""
    box = Scanner.create(names("cancel"))
    pending = box.move(1.0)
    box.close()
    with pytest.raises(CancelledError):
        pending.result(timeout=10)
    with pytest.raises(BoxClosedError):
        box.move(1.0)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no fork")
def test_a_forked_child_serves_nothing_it_inherited(
    names: Callable[[str], str],
) -> None:
    """Answer a forked child's call from the parent's server, never from the child."""
    count: Synchronized[int] = mp.get_context("fork").Value("i", 0)
    with Scanner.create(names("fork")) as box:
        box.move.serve(partial(counted_double, count))
        child = fork(ask_from_fork, box, count)
        child.join(30)
        assert child.exitcode == 0
        assert count.value == 1
        assert box.move(1.0).result(timeout=10) == 2.0
        assert count.value == 2
