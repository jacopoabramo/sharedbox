import logging
import multiprocessing as mp
import queue
import threading
import time
from collections.abc import Callable
from multiprocessing.synchronize import Event
from typing import Any

import pytest

from sharedbox import BoxClosedError, SharedBox


class Encoder(SharedBox):
    count: int = 0


class Motor(SharedBox):
    position: int = 0
    encoder: Encoder | None = None


class Stage(SharedBox):
    target: int = 0
    motor: Motor | None = None


def until(condition: Callable[[], bool], seconds: float = 10) -> bool:
    """Whether `condition` held within `seconds`, checked every 10 ms."""
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def settle(write: Callable[[int], object], seen: "queue.Queue[Any]") -> None:
    """Write 1, 2, ... through `write` until one is forwarded, then empty `seen`."""
    deadline = time.monotonic() + 10
    value = 0
    while True:
        value += 1
        write(value)
        try:
            seen.get(timeout=0.05)
            break
        except queue.Empty:
            assert time.monotonic() < deadline, "nothing was forwarded within 10 s"
    time.sleep(0.2)
    while not seen.empty():
        seen.get_nowait()


def threads(*boxes: SharedBox) -> list[int]:
    """Watcher threads of this process for each box's segment."""
    alive = [thread.name for thread in threading.enumerate()]
    return [alive.count(f"sharedbox-watch-{box.name}") for box in boxes]


def waiters(*boxes: SharedBox) -> list[int]:
    # The waiter count is not public; it is how the tests see the slots forwarding holds.
    return [box._segment._waiters for box in boxes]


def write_then_reassign(
    stage: Stage, motor_name: str, written: Event, go: Event
) -> None:
    with stage, Motor.attach(motor_name) as motor:
        before = stage.motor
        assert before is not None
        before.position = 1
        written.set()
        assert go.wait(10)
        stage.motor = motor


def test_follow_forwards_writes_from_another_process_and_moves_on_reassignment(
    names: Callable[[str], str],
) -> None:
    """Check that follow(field) forwards a write from another process, moves when that process reassigns the field, and forwards nothing from the old box afterwards."""
    ctx = mp.get_context("spawn")
    written, go = ctx.Event(), ctx.Event()
    seen: queue.Queue[tuple[int, int]] = queue.Queue()
    with (
        Motor.create(names("a")) as a,
        Motor.create(names("b")) as b,
        Stage.create(names("s"), 0, a) as stage,
    ):
        motor_events = stage.events.follow("motor")
        assert stage.events.follow("motor") is motor_events
        assert "position" in motor_events and "target" not in motor_events
        motor_events.position.connect(lambda new, old: seen.put((new, old)))
        child = ctx.Process(
            target=write_then_reassign, args=(stage, b.name, written, go)
        )
        child.start()
        assert written.wait(20)
        assert seen.get(timeout=5) == (1, 0)
        go.set()
        child.join(20)
        assert child.exitcode == 0
        settle(lambda value: setattr(b, "position", value), seen)
        a.position = 50
        b.position = 60
        assert seen.get(timeout=5)[0] == 60
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)


def test_the_reference_signal_runs_before_forwarding_moves(
    names: Callable[[str], str],
) -> None:
    """Check that the reference signal's callbacks run before forwarding moves, so a value they write to the new box is not forwarded."""
    seen: queue.Queue[int] = queue.Queue()
    forwarded: list[int] = []

    def record(new: int) -> None:
        forwarded.append(new)
        seen.put(new)

    with (
        Motor.create(names("a")) as a,
        Motor.create(names("b")) as b,
        Stage.create(names("s"), 0, a) as stage,
    ):
        stage.events.motor.connect(lambda new, old: setattr(b, "position", 99))
        stage.events.follow("motor").position.connect(record)
        stage.motor = b
        settle(lambda value: setattr(b, "position", 100 + value), seen)
        assert forwarded
        assert 99 not in forwarded


def test_an_empty_reference_forwards_nothing_until_a_box_is_assigned(
    names: Callable[[str], str],
) -> None:
    """Check that following an empty reference forwards nothing, and forwards from the box assigned later."""
    seen: queue.Queue[tuple[int, int]] = queue.Queue()
    with Motor.create(names("m")) as motor, Stage.create(names("s")) as stage:
        stage.events.follow("motor").position.connect(
            lambda new, old: seen.put((new, old))
        )
        motor.position = 1
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)
        stage.motor = motor
        settle(lambda value: setattr(motor, "position", value), seen)
        motor.position = 1000
        assert seen.get(timeout=5)[0] == 1000


def test_a_follow_group_follows_one_level_further(
    names: Callable[[str], str],
) -> None:
    """Check that a group returned by follow follows its own reference fields and moves when either level is reassigned."""
    seen: queue.Queue[tuple[int, int]] = queue.Queue()
    with (
        Encoder.create(names("e1")) as e1,
        Encoder.create(names("e2")) as e2,
        Encoder.create(names("e3")) as e3,
        Motor.create(names("a"), 0, e1) as a,
        Motor.create(names("b"), 0, e3) as b,
        Stage.create(names("s"), 0, a) as stage,
    ):
        counts = stage.events.follow("motor").follow("encoder")
        counts.count.connect(lambda new, old: seen.put((new, old)))
        e1.count = 1
        assert seen.get(timeout=5) == (1, 0)
        a.encoder = e2
        settle(lambda value: setattr(e2, "count", value), seen)
        stage.motor = b
        settle(lambda value: setattr(e3, "count", value), seen)
        e1.count = -1
        e2.count = -2
        e3.count = 3000
        assert seen.get(timeout=5)[0] == 3000
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)


def test_follow_refuses_a_field_that_is_not_a_reference(
    names: Callable[[str], str],
) -> None:
    """Check that follow and unfollow raise TypeError for a scalar field and ValueError for an unknown one, also on a closed box."""
    with Stage.create(names("s")) as stage:
        with pytest.raises(TypeError, match=r"Stage\.target is not a reference field"):
            stage.events.follow("target")
        with pytest.raises(TypeError, match=r"Stage\.target is not a reference field"):
            stage.events.unfollow("target")
        with pytest.raises(ValueError, match="Stage has no field 'nope'"):
            stage.events.follow("nope")
        motor_events = stage.events.follow("motor")
        with pytest.raises(TypeError, match=r"Motor\.position is not a reference"):
            motor_events.follow("position")
    with pytest.raises(BoxClosedError):
        stage.events.follow("motor")


def test_a_field_cannot_take_a_name_of_the_events_group() -> None:
    """Check that a field named follow, unfollow or nested is refused when the class is defined."""
    with pytest.raises(TypeError, match="nested clash with the events group"):

        class Bad(SharedBox):
            nested: int = 0


def test_unfollow_and_close_release_the_threads_and_waiter_slots(
    names: Callable[[str], str],
) -> None:
    """Check that unfollow(field), unfollow() and close() give back the threads and waiter slots follow(field) took, and unfollow leaves the handle reads use open."""
    with (
        Encoder.create(names("e")) as encoder,
        Motor.create(names("m"), 0, encoder) as motor,
    ):
        stage = Stage.create(names("s"), 0, motor)
        read = stage.motor
        assert read is not None
        assert (threads(motor, encoder), waiters(motor, encoder)) == ([0, 0], [0, 0])
        group = stage.events.follow("motor")
        assert until(lambda: waiters(motor, encoder) == [1, 0])
        assert threads(motor, encoder) == [1, 0]
        stage.events.unfollow("motor")
        assert (threads(motor, encoder), waiters(motor, encoder)) == ([0, 0], [0, 0])
        assert stage.events.follow("motor") is not group
        stage.events.follow("motor").follow("encoder")
        assert until(lambda: waiters(motor, encoder) == [1, 1])
        assert threads(motor, encoder) == [1, 1]
        stage.events.unfollow()
        assert (threads(motor, encoder), waiters(motor, encoder)) == ([0, 0], [0, 0])
        assert not read.closed
        stage.events.follow("motor").follow("encoder")
        assert until(lambda: waiters(motor, encoder) == [1, 1])
        stage.close()
        assert (threads(motor, encoder), waiters(motor, encoder)) == ([0, 0], [0, 0])


class GearMotor(Motor):
    gear: int = 0


def test_a_box_of_a_subclass_forwards_the_signals_of_the_annotated_class(
    names: Callable[[str], str], caplog: pytest.LogCaptureFixture
) -> None:
    """Check that follow(field) forwards the signals of the annotated class for a box of a subclass and ignores the fields only the subclass has."""
    seen: queue.Queue[int] = queue.Queue()
    with (
        GearMotor.create(names("g")) as geared,
        Stage.create(names("s"), 0, geared) as stage,
        caplog.at_level(logging.WARNING, logger="sharedbox"),
    ):
        group = stage.events.follow("motor")
        assert "gear" not in group
        group.position.connect(lambda new, old: seen.put(new))
        geared.gear = 3
        geared.position = 2
        assert seen.get(timeout=5) == 2
    assert not caplog.records


def test_a_raising_reference_callback_does_not_stop_forwarding_from_moving(
    names: Callable[[str], str],
) -> None:
    """Check that forwarding still moves when a callback of the reference signal raises."""
    seen: queue.Queue[tuple[int, int]] = queue.Queue()

    def fail(new: object) -> None:
        raise RuntimeError("boom")

    with (
        Motor.create(names("a")) as a,
        Motor.create(names("b")) as b,
        Stage.create(names("s"), 0, a) as stage,
    ):
        stage.events.motor.connect(fail)
        stage.events.follow("motor").position.connect(
            lambda new, old: seen.put((new, old))
        )
        stage.motor = b
        settle(lambda value: setattr(b, "position", value), seen)


def test_closing_the_box_from_a_forwarded_callback_releases_everything(
    names: Callable[[str], str],
) -> None:
    """Check that a forwarded callback can close the outer box without deadlock, and the forwarding thread and slot are released."""
    with Motor.create(names("m")) as motor:
        stage = Stage.create(names("s"), 0, motor)
        stage.events.follow("motor").position.connect(lambda new: stage.close())
        motor.position = 1
        assert until(lambda: stage.closed)
        assert until(lambda: threads(motor) == [0])
        assert waiters(motor) == [0]


class Narrow(SharedBox, max_waiters=1):
    value: int = 0


class Holder(SharedBox):
    narrow: Narrow | None = None


def test_following_a_box_whose_waiter_slots_are_taken_still_forwards(
    names: Callable[[str], str], caplog: pytest.LogCaptureFixture
) -> None:
    """Check that following a box with every waiter slot taken warns and still forwards, checking once a second."""
    seen: queue.Queue[tuple[int, int]] = queue.Queue()
    with (
        Narrow.create(names("n")) as narrow,
        Holder.create(names("h"), narrow) as holder,
        caplog.at_level(logging.WARNING, logger="sharedbox"),
    ):
        narrow.events.value.connect(lambda new: None)
        assert until(lambda: waiters(narrow) == [1])
        holder.events.follow("narrow").value.connect(
            lambda new, old: seen.put((new, old))
        )
        narrow.value = 5
        assert seen.get(timeout=5) == (5, 0)
        assert until(
            lambda: any("waiter slots" in r.getMessage() for r in caplog.records)
        )


def test_reassigning_while_both_boxes_change_leaves_one_forwarding_handle(
    names: Callable[[str], str], caplog: pytest.LogCaptureFixture
) -> None:
    """Check that reassigning the reference many times while both boxes are written leaves forwarding on the last box with one thread and one slot, and logs no error."""
    seen: queue.Queue[int] = queue.Queue()
    stop = threading.Event()

    def churn(box: Motor) -> None:
        value = 0
        while not stop.is_set():
            value += 1
            box.position = value

    with (
        Motor.create(names("a")) as a,
        Motor.create(names("b")) as b,
        Stage.create(names("s"), 0, a) as stage,
        caplog.at_level(logging.ERROR, logger="sharedbox"),
    ):
        stage.events.follow("motor").position.connect(lambda new, old: seen.put(new))
        writers = [threading.Thread(target=churn, args=(box,)) for box in (a, b)]
        for writer in writers:
            writer.start()
        try:
            for i in range(200):
                stage.motor = b if i % 2 == 0 else a
                time.sleep(0.002)
            stage.motor = b
        finally:
            stop.set()
            for writer in writers:
                writer.join(10)
        assert until(lambda: threads(a, b) == [0, 1])
        assert until(lambda: waiters(a, b) == [0, 1])
        settle(lambda value: setattr(b, "position", -value), seen)
        a.position = 0
        b.position = 1
        assert seen.get(timeout=5) == 1
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)
    assert not caplog.records
