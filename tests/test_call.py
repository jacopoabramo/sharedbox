import asyncio
import contextlib
import gc
import logging
import multiprocessing as mp
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass
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
    CallServer,
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
        assert len(text.encode()) == 256
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
    seen: list[float] = []

    def record(position: float) -> float:
        seen.append(position)
        return position

    with Scanner.create(names("arguments")) as box:
        box.move.serve(record)
        with pytest.raises(TypeError, match="positional arguments only"):
            box.move(position=1.0)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            box.move("x")  # type: ignore[arg-type]
        assert box.move(1.0).result(timeout=10) == 1.0
        assert seen == [1.0]


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


class Counter(SharedBox):
    bump: Call[[int], int] = field(init=False)


def too_large(count: int) -> int:
    return 2**64


def test_a_result_that_cannot_be_stored_fails_the_call(
    names: Callable[[str], str],
) -> None:
    """Fail a call whose handler returns a value its result type cannot hold."""
    with Counter.create(names("overflow")) as box:
        box.bump.serve(too_large)
        with pytest.raises(CallError, match="OverflowError"):
            box.bump(1).result(timeout=10)


def test_closing_cancels_a_call_a_handler_waits_on(
    names: Callable[[str], str],
) -> None:
    """Cancel, on close, a call that a sync handler of the same box waits on."""
    waiting = threading.Event()
    seen: list[str] = []
    box = Scanner.create(names("nested"))

    def home() -> None:
        pending = box.move(0.0)
        waiting.set()
        try:
            pending.result(timeout=10)
        except CancelledError:
            seen.append("cancelled")
        except TimeoutError:
            seen.append("timed out")

    box.home.serve(home)
    box.home()
    assert waiting.wait(10)
    box.close()
    assert seen == ["cancelled"]


def test_a_server_stopped_inside_its_take_fails_the_request(
    names: Callable[[str], str],
) -> None:
    """Fail the request a server took while stopping it from inside its take."""
    servers: list[CallServer] = []

    @dataclass(frozen=True)
    class Target:
        position: float

        def __post_init__(self) -> None:
            # Decoding the request runs this inside the server's take, as a finalizer may.
            if servers:
                servers.pop().stop()

    class Mover(SharedBox):
        move: Call[[Target], float] = field(init=False)

    with Mover.create(names("inside")) as box:
        target = Target(1.0)
        servers.append(box.move.serve(lambda target: target.position))
        with pytest.raises(CallError, match="the server stopped"):
            box.move(target).result(timeout=10)
        box.move.serve(lambda target: target.position * 2)
        assert box.move(target).result(timeout=10) == 2.0


def surrogate(position: float) -> float:
    raise ValueError("\ud800")


def leave(position: float) -> float:
    raise SystemExit(3)


def test_a_result_that_fails_to_decode_reaches_the_caller(
    names: Callable[[str], str],
) -> None:
    """Give the caller the error its result's decoding raised, and answer later calls."""
    refuse: list[bool] = []

    @dataclass(frozen=True)
    class Reading:
        value: float

        def __post_init__(self) -> None:
            if refuse:
                raise ValueError("refused")

    class Probe(SharedBox):
        read: Call[[], Reading] = field(init=False)

    reading = Reading(1.0)
    with Probe.create(names("decode")) as box:
        box.read.serve(lambda: reading)
        refuse.append(True)
        with pytest.raises(ValueError, match="refused"):
            box.read().result(timeout=5)
        refuse.clear()
        assert box.read().result(timeout=5) == reading


def test_a_timeout_names_the_field_and_the_request(
    names: Callable[[str], str],
) -> None:
    """Name the field and the request in the TimeoutError of a call nobody answers."""
    with (
        Scanner.create(names("timeout")) as box,
        pytest.raises(
            TimeoutError, match="Scanner.move: no answer to request 1 within 0.1 s"
        ),
    ):
        box.move(1.0).result(timeout=0.1)


def test_stopping_a_server_from_a_watcher_callback_does_not_wait(
    names: Callable[[str], str],
) -> None:
    """Return from stop() in an event callback while the sync handler still runs."""
    waiting = threading.Event()
    stopped = threading.Event()
    with Scanner.create(names("callback")) as box:

        def home() -> None:
            pending = box.move(0.0)
            waiting.set()
            with contextlib.suppress(CancelledError, TimeoutError):
                pending.result(timeout=10)

        server = box.home.serve(home)

        def on_position(new: float, old: float) -> None:
            server.stop()
            stopped.set()

        box.events.position.connect(on_position)
        box.home()
        assert waiting.wait(10)
        box.position = 1.0
        assert stopped.wait(5)


def test_a_surrogate_in_a_handler_error_reaches_the_caller(
    names: Callable[[str], str],
) -> None:
    """Escape a lone surrogate in the handler's error text."""
    with Scanner.create(names("surrogate")) as box:
        box.move.serve(surrogate)
        with pytest.raises(CallError, match=r"ValueError: \\ud800"):
            box.move(1.0).result(timeout=5)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_handler_that_exits_fails_its_call_and_frees_the_field(
    names: Callable[[str], str],
) -> None:
    """Fail the call of a sync handler raising SystemExit, and free the field."""
    with Scanner.create(names("exits")) as box:
        box.move.serve(leave)
        with pytest.raises(CallError, match="SystemExit"):
            box.move(1.0).result(timeout=5)
        # Waited for, so pytest reports the thread's SystemExit within this test.
        for thread in threading.enumerate():
            if thread.name == "sharedbox-serve-Scanner.move":
                thread.join(5)
        box.move.serve(double)
        assert box.move(1.0).result(timeout=5) == 2.0


def test_closing_the_box_inside_a_take_frees_the_field(
    names: Callable[[str], str],
) -> None:
    """Fail the taken request and free the field when the box closes inside its take."""
    boxes: list[SharedBox] = []

    @dataclass(frozen=True)
    class Target:
        position: float

        def __post_init__(self) -> None:
            # Closing here, inside the server's take, does what the box's finalizer does.
            if boxes:
                boxes.pop().close()

    class Mover(SharedBox):
        move: Call[[Target], float] = field(init=False)

    name = names("closing")
    target = Target(1.0)
    with Mover.create(name) as other:
        box = Mover.attach(name)
        box.move.serve(lambda target: target.position)
        boxes.append(box)
        pending = other.move(target)
        with pytest.raises(CallError, match="the server stopped"):
            pending.result(timeout=5)
        other.move.serve(lambda target: target.position * 2)
        assert other.move(target).result(timeout=5) == 2.0


class Guarded(SharedBox, lock_timeout=0.2):
    move: Call[[float], float] = field(init=False)


def test_a_lock_timeout_keeps_a_call_pending(
    names: Callable[[str], str], caplog: pytest.LogCaptureFixture
) -> None:
    """Keep a call pending while the lock is held past its timeout, and deliver its answer after."""
    with Guarded.create(names("locked")) as box, caplog.at_level(logging.WARNING):
        pending = box.move(1.0)
        box._segment._hold_write_lock()
        deadline = time.monotonic() + 5
        while (
            "locked" not in caplog.text
            and not pending.done()
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        box.force_unlock()
        box.move.serve(double)
        assert pending.result(timeout=5) == 2.0
