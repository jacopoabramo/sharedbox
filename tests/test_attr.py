import asyncio
import contextlib
import gc
import logging
import multiprocessing as mp
import queue
import sys
import threading
import time
from collections.abc import Callable, Coroutine, Generator
from concurrent.futures import CancelledError
from dataclasses import dataclass
from functools import partial
from multiprocessing.connection import Connection
from multiprocessing.sharedctypes import Synchronized
from multiprocessing.synchronize import Event as EventType
from typing import Any, TypeVar

import pytest
from crossproc import call_in_child, fork, serve_once, serving_in_child
from test_attr_fields import CLOCK_SLACK_NS, Camera

from sharedbox import (
    AlreadyServedError,
    Attr,
    BoxClosedError,
    RequestError,
    RequestServer,
    RequestSupersededError,
    ServerExitedError,
    SharedBox,
    attr,
)

T = TypeVar("T")


class Scanner(SharedBox):
    position: float = 0.0
    move: Attr[float, [float]] = attr()
    home: Attr[None] = attr()


class Counter(SharedBox):
    bump: Attr[int, [int]] = attr()


class Guarded(SharedBox, lock_timeout=0.2):
    move: Attr[float, [float]] = attr()


class Zero(SharedBox):
    x: Attr[float, []] = attr()


def own_camera(name: str, ready: EventType, stop: Connection) -> None:
    with Camera.create(name) as camera:

        def set_exposure(value: float) -> float:
            camera.exposure.publish(setpoint=value)
            return value

        def home() -> None:
            camera.exposure.publish(value=camera.exposure.setpoint)

        camera.exposure.serve(set_exposure)
        camera.move.serve(lambda position, speed: position)
        camera.home.serve(home)
        ready.set()
        with contextlib.suppress(EOFError):
            stop.recv()


@contextlib.contextmanager
def camera_in_child(name: str) -> Generator[Camera, None, None]:
    """Attach to a `Camera` that a spawned process creates and serves until the block ends."""
    context = mp.get_context("spawn")
    ready = context.Event()
    stop, stopping = context.Pipe(duplex=False)
    child = context.Process(target=own_camera, args=(name, ready, stop))
    child.start()
    try:
        while not ready.wait(0.1):
            assert child.is_alive(), "the child exited before it served"
        with Camera.attach(name) as camera:
            yield camera
    finally:
        stopping.close()
        child.join(10)
        if child.is_alive():
            child.kill()
            child.join(10)


async def awaited(future: Any) -> Any:
    return await future


def test_an_attr_across_processes_publishes_and_answers_requests(
    names: Callable[[str], str],
) -> None:
    """Answer set, a request with arguments and a command, and emit one event per publish only."""
    exposures: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
    moves: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
    with camera_in_child(names("camera")) as camera:
        camera.events.exposure.connect(lambda new, old: exposures.put(new))
        camera.events.move.connect(lambda new, old: moves.put(new))
        assert asyncio.run(awaited(camera.exposure.set(0.02))) == 0.02
        assert exposures.get(timeout=5) == {
            "value": 0.0,
            "setpoint": 0.02,
            "timestamp_ns": 0,
        }
        assert camera.move(1.0, "fast").result(timeout=5.0) == 1.0
        before = time.time_ns()
        assert asyncio.run(awaited(camera.home())) is None
        after = time.time_ns() + CLOCK_SLACK_NS
        homed = exposures.get(timeout=5)
        assert homed["value"] == 0.02
        assert before <= homed["timestamp_ns"] <= after
        assert exposures.empty()
        assert moves.empty()


def test_set_sends_the_value_as_its_argument(names: Callable[[str], str]) -> None:
    """Pass the value given to set to the handler as its one argument."""
    seen: list[float] = []

    def record(value: float) -> float:
        seen.append(value)
        return value

    with Camera.create(names("set")) as box:
        box.exposure.serve(record)
        assert box.exposure.set(3.0).result(timeout=10) == 3.0
        assert seen == [3.0]


@pytest.mark.parametrize(
    ("cls", "send", "message"),
    [
        (Camera, lambda box: box.temperature(), "Camera.temperature takes no requests"),
        (Camera, lambda box: box.move.set(1.0), "Camera.move is not settable"),
        (
            Camera,
            lambda box: box.temperature.set(1.0),
            "Camera.temperature is not settable",
        ),
        (Zero, lambda box: box.x(), "Zero.x takes no requests"),
        (
            Camera,
            lambda box: box.exposure(1.0),
            "Camera.exposure is settable; send its requests with set()",
        ),
    ],
    ids=[
        "call without a request",
        "set on a request",
        "set on a value",
        "no arguments",
        "call on a settable",
    ],
)
def test_a_request_on_an_attr_without_one_is_refused(
    names: Callable[[str], str],
    cls: type[SharedBox],
    send: Callable[[Any], Any],
    message: str,
) -> None:
    """Raise TypeError naming the field for a call or a set that the attribute has no request for."""
    with cls.create(names("refused")) as box, pytest.raises(TypeError, match=message):
        send(box)


def test_a_watch_resolves_on_a_publish_after_a_request(
    names: Callable[[str], str],
) -> None:
    """Leave a watch pending through a request and resolve it on the next publish."""
    with Camera.create(names("watch")) as box:
        fut = box.watch("move").future()
        box.move.serve(lambda position, speed: position)
        assert box.move(1.0, "fast").result(timeout=5) == 1.0
        assert not fut.done()
        box.move.publish(value=2.0)
        assert fut.result(timeout=5) == {"value": 2.0}


def test_watch_and_events_refuse_an_attr_without_a_value(
    names: Callable[[str], str],
) -> None:
    """Raise TypeError for a watch of or a signal on an Attr[None]."""
    with Camera.create(names("none")) as box:
        with pytest.raises(TypeError):
            box.watch("home")
        with pytest.raises(TypeError):
            box.events.home  # noqa: B018


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


def test_a_request_across_processes_is_answered_and_superseded(
    names: Callable[[str], str],
) -> None:
    """Answer awaited, blocking and kept requests, and supersede a request sent while another runs."""
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
            with pytest.raises(RequestSupersededError):
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
        with pytest.raises(AlreadyServedError):
            box.move.serve(double)
        server.stop()
        with serving_in_child(box, move=double), pytest.raises(AlreadyServedError):
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
    """Give the caller a RequestError with the handler's error, cut to 256 bytes, and log it."""
    with Scanner.create(names("raises")) as box, caplog.at_level(logging.ERROR):
        server = box.move.serve(too_far)
        with pytest.raises(RequestError) as raised:
            box.move(1.0).result(timeout=10)
        assert str(raised.value).endswith("ValueError: too far")
        server.stop()
        box.move.serve(long_message)
        with pytest.raises(RequestError) as raised:
            box.move(1.0).result(timeout=10)
        text = str(raised.value).removeprefix("Scanner.move: ")
        assert text.startswith("ValueError: é")
        assert len(text.encode()) == 256
    logged = [
        r
        for r in caplog.records
        if r.name == "sharedbox" and "Traceback" in r.getMessage()
    ]
    assert len(logged) == 2


def test_a_request_sent_before_serve_is_answered(names: Callable[[str], str]) -> None:
    """Answer a request that was sent before anyone served the field."""
    with Scanner.create(names("early")) as box:
        pending = box.move(1.5)
        box.move.serve(double)
        assert pending.result(timeout=10) == 3.0


def test_a_request_refuses_keywords_and_wrong_arguments(
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


def test_closing_the_box_cancels_pending_requests(names: Callable[[str], str]) -> None:
    """Cancel a pending request when the box closes, and refuse a request on a closed box."""
    box = Scanner.create(names("cancel"))
    pending = box.move(1.0)
    move = box.move
    box.close()
    with pytest.raises(CancelledError):
        pending.result(timeout=10)
    with pytest.raises(BoxClosedError):
        box.move(1.0)
    with pytest.raises(BoxClosedError):
        move(1.0)


def test_a_request_through_the_handle_of_a_freed_box_raises(
    names: Callable[[str], str],
) -> None:
    """Raise BoxClosedError for a request, a set and a serve once the box is gone."""
    box = Camera.create(names("freed"))
    move, exposure = box.move, box.exposure
    del box
    with pytest.raises(BoxClosedError):
        move(1.0, "fast")
    with pytest.raises(BoxClosedError):
        exposure.set(1.0)
    with pytest.raises(BoxClosedError):
        move.serve(lambda position, speed: position)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no fork")
def test_a_forked_child_serves_nothing_it_inherited(
    names: Callable[[str], str],
) -> None:
    """Answer a forked child's request from the parent's server, never from the child."""
    count: Synchronized[int] = mp.get_context("fork").Value("i", 0)
    with Scanner.create(names("fork")) as box:
        box.move.serve(partial(counted_double, count))
        child = fork(ask_from_fork, box, count)
        child.join(30)
        assert child.exitcode == 0
        assert count.value == 1
        assert box.move(1.0).result(timeout=10) == 2.0
        assert count.value == 2


def too_large(count: int) -> int:
    return 2**64


def test_a_result_that_cannot_be_stored_fails_the_request(
    names: Callable[[str], str],
) -> None:
    """Fail a request whose handler returns a value its result type cannot hold."""
    with Counter.create(names("overflow")) as box:
        box.bump.serve(too_large)
        with pytest.raises(RequestError, match="OverflowError"):
            box.bump(1).result(timeout=10)


def test_closing_cancels_a_request_a_handler_waits_on(
    names: Callable[[str], str],
) -> None:
    """Cancel, on close, a request that a sync handler of the same box waits on."""
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
    servers: list[RequestServer] = []

    @dataclass(frozen=True)
    class Target:
        position: float

        def __post_init__(self) -> None:
            # Decoding the request runs this inside the server's take, as a finalizer may.
            if servers:
                servers.pop().stop()

    class Mover(SharedBox):
        move: Attr[float, [Target]] = attr()

    with Mover.create(names("inside")) as box:
        target = Target(1.0)
        servers.append(box.move.serve(lambda target: target.position))
        with pytest.raises(RequestError, match="the server stopped"):
            box.move(target).result(timeout=10)
        box.move.serve(lambda target: target.position * 2)
        assert box.move(target).result(timeout=10) == 2.0


def test_a_claim_made_inside_the_take_that_stopped_its_server_holds(
    names: Callable[[str], str],
) -> None:
    """Keep a claim made inside the take that stopped the field's previous server."""
    reclaims: list[Callable[[], RequestServer]] = []
    servers: list[RequestServer] = []
    taking: list[threading.Thread] = []

    @dataclass(frozen=True)
    class Target:
        position: float

        def __post_init__(self) -> None:
            if servers:
                taking.append(threading.current_thread())
                servers.pop().stop()
                reclaims.pop()()

    class Mover(SharedBox):
        move: Attr[float, [Target]] = attr()

    with Mover.create(names("reclaim")) as box:
        reclaims.append(lambda: box.move.serve(lambda target: target.position * 2))
        target = Target(1.0)
        servers.append(box.move.serve(lambda target: target.position))
        with pytest.raises(RequestError, match="the server stopped"):
            box.move(target).result(timeout=10)
        # The stopped server's thread finishes its take after the caller has its answer.
        taking[0].join(10)
        with pytest.raises(AlreadyServedError):
            box.move.serve(lambda target: target.position)
        assert box.move(target).result(timeout=10) == 2.0


def surrogate(position: float) -> float:
    raise ValueError("\ud800")


def leave(position: float) -> float:
    raise SystemExit(3)


def test_a_result_that_fails_to_decode_reaches_the_caller(
    names: Callable[[str], str],
) -> None:
    """Give the caller the error its result's decoding raised, and answer later requests."""
    refuse: list[bool] = []

    @dataclass(frozen=True)
    class Reading:
        value: float

        def __post_init__(self) -> None:
            if refuse:
                raise ValueError("refused")

    class Probe(SharedBox):
        sample: Attr[Reading, [int]] = attr(default=Reading(0.0))

    reading = Reading(1.0)
    with Probe.create(names("decode")) as box:
        box.sample.serve(lambda channel: reading)
        refuse.append(True)
        with pytest.raises(ValueError, match="refused"):
            box.sample(0).result(timeout=5)
        refuse.clear()
        assert box.sample(0).result(timeout=5) == reading


def test_a_timeout_names_the_field_and_the_request(
    names: Callable[[str], str],
) -> None:
    """Name the field and the request in the TimeoutError of a request nobody answers."""
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
        with pytest.raises(RequestError, match=r"ValueError: \\ud800"):
            box.move(1.0).result(timeout=5)


def test_a_surrogate_in_a_handler_error_is_escaped_in_the_log(
    names: Callable[[str], str], caplog: pytest.LogCaptureFixture
) -> None:
    """Log the error of a handler, with its traceback, as text that UTF-8 can encode."""
    with (
        Scanner.create(names("logged")) as box,
        caplog.at_level(logging.ERROR, logger="sharedbox"),
    ):
        box.move.serve(surrogate)
        with pytest.raises(RequestError):
            box.move(1.0).result(timeout=5)
    logged = caplog.text.encode("utf-8").decode()
    assert ("Traceback" in logged, "ValueError: \\ud800" in logged) == (True, True)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_handler_that_exits_fails_its_request_and_frees_the_field(
    names: Callable[[str], str],
) -> None:
    """Fail the request of a sync handler raising SystemExit, and free the field."""
    with Scanner.create(names("exits")) as box:
        box.move.serve(leave)
        with pytest.raises(RequestError, match="SystemExit"):
            box.move(1.0).result(timeout=5)
        # Waited for, so pytest reports the thread's SystemExit within this test.
        for thread in threading.enumerate():
            if thread.name == "sharedbox-serve-Scanner.move":
                thread.join(5)
        box.move.serve(double)
        assert box.move(1.0).result(timeout=5) == 2.0


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_handler_that_exits_frees_the_field_when_failing_its_request_raises(
    names: Callable[[str], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop serving and free the field after SystemExit even when failing the request raises."""

    def refuse(self: RequestServer, number: int, error: BaseException) -> None:
        raise RuntimeError("the answer cannot be written")

    monkeypatch.setattr("sharedbox._request.RequestServer._fail", refuse)
    with Scanner.create(names("exits-twice")) as box:
        box.move.serve(leave)
        with pytest.raises(RequestError, match="the server stopped"):
            box.move(1.0).result(timeout=5)
        for thread in threading.enumerate():
            if thread.name == "sharedbox-serve-Scanner.move":
                thread.join(5)
                assert not thread.is_alive()
        box.move.serve(double)
        assert box.move(1.0).result(timeout=5) == 2.0


def test_a_second_stop_waits_until_the_first_has_freed_the_field(
    names: Callable[[str], str],
) -> None:
    """Return from a stop racing another stop only once the field is free to serve again."""
    started = threading.Event()
    release = threading.Event()

    def slow(position: float) -> float:
        started.set()
        release.wait(10)
        return position

    with Scanner.create(names("two-stops")) as box:
        server = box.move.serve(slow)
        pending = box.move(1.0)
        assert started.wait(10)
        stoppers = [threading.Thread(target=server.stop) for _ in range(2)]
        for stopper in stoppers:
            stopper.start()
        # One stop joins the running handler; the other must wait for it, not return.
        for stopper in stoppers:
            stopper.join(0.3)
        assert all(stopper.is_alive() for stopper in stoppers)
        release.set()
        for stopper in stoppers:
            stopper.join(10)
        assert pending.result(timeout=5) == 1.0
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
        move: Attr[float, [Target]] = attr()

    name = names("closing")
    target = Target(1.0)
    with Mover.create(name) as other:
        box = Mover.attach(name)
        box.move.serve(lambda target: target.position)
        boxes.append(box)
        pending = other.move(target)
        with pytest.raises(RequestError, match="the server stopped"):
            pending.result(timeout=5)
        other.move.serve(lambda target: target.position * 2)
        assert other.move(target).result(timeout=5) == 2.0


def test_a_lock_timeout_keeps_a_request_pending(
    names: Callable[[str], str], caplog: pytest.LogCaptureFixture
) -> None:
    """Keep a request pending while the lock is held past its timeout, and deliver its answer after."""
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


async def serve_on(
    attribute: Attr[Any, ...], handler: Callable[..., Any]
) -> RequestServer:
    return attribute.serve(handler)


async def double_later(position: float) -> float:
    return position * 2


def test_an_async_handler_can_gather_tasks(names: Callable[[str], str]) -> None:
    """Answer with the sum of two tasks an async handler gathers."""

    async def handler(position: float) -> float:
        first = asyncio.create_task(double_later(position))
        second = asyncio.create_task(double_later(position + 1))
        return sum(await asyncio.gather(first, second))

    with running_loop() as loop, Scanner.create(names("gather")) as box:
        on_loop(loop, serve_on(box.move, handler))
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
        on_loop(loop, serve_on(box.move, handler))
        assert box.move(1.0).result(timeout=10) == 1.0
        assert box.move(2.0).result(timeout=10) == 2.0
        assert on_loop(loop, task_running())
        loop.call_soon_threadsafe(release.set)
        on_loop(loop, task_finished())


def test_cancelled_loop_tasks_give_the_caller_request_error(
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
        with pytest.raises(RequestError, match="the handler was cancelled"):
            pending.result(timeout=10)
        assert call_in_child(serve_once, box, "move") is True


def test_a_handler_can_await_another_request_on_the_same_box(
    names: Callable[[str], str],
) -> None:
    """Answer a handler that awaits a request to another field served on the same loop."""
    with running_loop() as loop, Scanner.create(names("nested")) as box:

        async def home() -> None:
            assert await box.move(0.0) == 0.0

        on_loop(loop, serve_on(box.move, double_later))
        on_loop(loop, serve_on(box.home, home))
        assert box.home().result(timeout=10) is None


def test_an_async_handler_needs_a_running_loop(names: Callable[[str], str]) -> None:
    """Refuse to serve an async handler outside a running event loop."""
    with Scanner.create(names("noloop")) as box:
        with pytest.raises(RuntimeError, match="running event loop"):
            box.move.serve(double_later)
        box.move.serve(lambda position: position)


async def serve_idle(box: Scanner) -> None:
    box.move.serve(double_later)


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


def test_a_cancelled_error_from_the_handler_fails_only_that_request(
    names: Callable[[str], str],
) -> None:
    """Fail a request whose handler raised CancelledError itself, and answer the next."""

    async def handler(position: float) -> float:
        if position < 0:
            inner = asyncio.create_task(asyncio.sleep(60))
            await asyncio.sleep(0)
            inner.cancel()
            await inner
        return position

    with running_loop() as loop, Scanner.create(names("inner")) as box:
        on_loop(loop, serve_on(box.move, handler))
        with pytest.raises(RequestError, match="CancelledError"):
            box.move(-1.0).result(timeout=10)
        assert box.move(1.0).result(timeout=10) == 1.0


def test_stop_refused_in_a_locked_block_frees_the_field_when_repeated(
    names: Callable[[str], str],
) -> None:
    """Free the field with a second stop after one inside a locked() block raised."""
    with Scanner.create(names("stop")) as box:
        server = box.move.serve(lambda position: position)
        with box.locked(), pytest.raises(RuntimeError, match="locked"):
            server.stop()
        server.stop()
        box.move.serve(lambda position: position * 2)
        assert box.move(1.0).result(timeout=5) == 2.0


def test_close_cancels_futures_when_the_served_field_cannot_be_released(
    names: Callable[[str], str],
) -> None:
    """Cancel pending futures on close while a held lock keeps the served field from being released."""
    name = names("stuck")
    with Guarded.create(name) as holder:
        box = Guarded.attach(name)
        box.move.serve(double)
        future = box.watch("move").future()
        holder._segment._hold_write_lock()
        try:
            box.close()
        finally:
            holder._segment._release_held_lock()
        assert future.cancelled()


def test_result_in_a_callback_of_the_same_box_raises_at_once(
    names: Callable[[str], str],
) -> None:
    """Raise RuntimeError from result() of a pending request inside an events callback of its box."""
    seen: queue.Queue[type[BaseException] | float] = queue.Queue()

    def forward(new: float) -> None:
        try:
            seen.put(box.move(new).result(timeout=5))
        except RuntimeError as error:
            seen.put(type(error))

    with Scanner.create(names("callback")) as box:
        box.move.serve(lambda position: position)
        box.events.position.connect(forward)
        box.position = 2.0
        assert seen.get(timeout=4) is RuntimeError


def test_requests_nobody_serves_are_superseded_by_the_next_one(
    names: Callable[[str], str],
) -> None:
    """Fail each request a later one replaced while no server runs, and answer the newest once one starts."""
    with Scanner.create(names("unserved")) as box:
        first, second, newest = box.move(1.0), box.move(2.0), box.move(3.0)
        with pytest.raises(RequestSupersededError):
            first.result(timeout=5)
        with pytest.raises(RequestSupersededError):
            second.result(timeout=5)
        box.move.serve(lambda position: position)
        assert newest.result(timeout=5) == 3.0
