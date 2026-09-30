import logging
import multiprocessing as mp
import queue
import threading
import time
from collections.abc import Callable
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event
from typing import Any

import pytest

from sharedbox import BoxClosedError, BoxRef, SharedBox
from sharedbox._refs import attach_reference


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
    for name in ("follow", "unfollow", "nested"):
        with pytest.raises(TypeError, match=f"{name} clash with the events group"):
            type("Bad", (SharedBox,), {"__annotations__": {name: int}, name: 0})


def test_unfollow_and_close_release_the_threads_and_waiter_slots(
    names: Callable[[str], str],
) -> None:
    """Check that unfollow(field), unfollow() and close() give back the threads and waiter slots follow(field) took, and unfollow leaves the handle reads use open."""
    with (
        Encoder.create(names("e")) as encoder,
        Motor.create(names("m"), 0, encoder) as motor,
    ):
        stage = Stage.create(names("s"), 0, motor)
        try:
            read = stage.motor
            assert read is not None
            assert (threads(motor, encoder), waiters(motor, encoder)) == (
                [0, 0],
                [0, 0],
            )
            seen: queue.Queue[int] = queue.Queue()
            group = stage.events.follow("motor")
            group.position.connect(lambda new: seen.put(new))
            # The handle forwarding holds is not public; the test checks that unfollow closes it.
            held = group._sharedbox_follower.box
            assert held is not None
            assert until(lambda: waiters(motor, encoder) == [1, 0])
            assert threads(motor, encoder) == [1, 0]
            stage.events.unfollow("motor")
            assert held.closed
            assert (threads(motor, encoder), waiters(motor, encoder)) == (
                [0, 0],
                [0, 0],
            )
            motor.position = 1
            with pytest.raises(queue.Empty):
                seen.get(timeout=0.3)
            assert stage.events.follow("motor") is not group
            stage.events.follow("motor").follow("encoder")
            assert until(lambda: waiters(motor, encoder) == [1, 1])
            assert threads(motor, encoder) == [1, 1]
            stage.events.unfollow()
            assert (threads(motor, encoder), waiters(motor, encoder)) == (
                [0, 0],
                [0, 0],
            )
            assert not read.closed
            stage.events.follow("motor").follow("encoder")
            assert until(lambda: waiters(motor, encoder) == [1, 1])
            stage.close()
            assert (threads(motor, encoder), waiters(motor, encoder)) == (
                [0, 0],
                [0, 0],
            )
        finally:
            stage.close()


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
        try:
            stage.events.follow("motor").position.connect(lambda new: stage.close())
            motor.position = 1
            assert until(lambda: stage.closed)
            assert until(lambda: threads(motor) == [0])
            assert waiters(motor) == [0]
        finally:
            stage.close()


def act_during_a_move(level: int, action: str, box_names: tuple[str, ...]) -> None:
    """Close the stage or unfollow from a forwarded callback while forwarding moves off its box.

    The callback runs on the forwarding thread of the box being left, and
    acts once the move is joining that thread.
    """
    started, moving, acted = threading.Event(), threading.Event(), threading.Event()
    e1_name, e2_name, a_name, b_name, stage_name = box_names

    def act(new: int, old: int) -> None:
        started.set()
        assert moving.wait(10)
        # Long enough for the move to reach its join of this thread.
        time.sleep(0.3)
        if action == "close":
            stage.close()
        else:
            stage.events.unfollow()
        acted.set()

    with (
        Encoder.create(e1_name) as e1,
        Encoder.create(e2_name) as e2,
        Motor.create(a_name, 0, e1) as a,
        Motor.create(b_name, 0, e2) as b,
        Stage.create(stage_name, 0, a) as stage,
    ):
        motor_events = stage.events.follow("motor")
        if level == 1:
            stage.events.motor.connect(lambda new, old: moving.set())
            motor_events.position.connect(act)
            a.position = 1
            assert started.wait(10)
            stage.motor = b
        else:
            motor_events.encoder.connect(lambda new, old: moving.set())
            motor_events.follow("encoder").count.connect(act)
            e1.count = 1
            assert started.wait(10)
            a.encoder = e2
        assert acted.wait(10)
        assert until(lambda: threads(e1, e2, a, b) == [0, 0, 0, 0])
        assert waiters(e1, e2, a, b) == [0, 0, 0, 0]


@pytest.mark.parametrize(
    ("level", "action"), [(1, "close"), (2, "close"), (2, "unfollow")]
)
def test_closing_or_unfollowing_from_a_forwarded_callback_during_a_move_does_not_deadlock(
    names: Callable[[str], str], level: int, action: str
) -> None:
    """Check that a forwarded callback can close the outer box or unfollow while a move joins its thread, at the first or second level."""
    ctx = mp.get_context("spawn")
    box_names = tuple(names(suffix) for suffix in ("e1", "e2", "a", "b", "s"))
    child = ctx.Process(target=act_during_a_move, args=(level, action, box_names))
    child.start()
    child.join(30)
    if child.is_alive():
        child.kill()
        child.join(10)
    assert child.exitcode == 0


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


class Node(SharedBox):
    value: int = 0
    link: "Node | None" = None


class Pair(SharedBox):
    first: Motor | None = None
    second: Motor | None = None


def ref_of(box: SharedBox) -> BoxRef:
    # The create id is not public; the test needs it to build the BoxRef an event reports.
    return BoxRef(box.name, type(box).__layout__.schema_hash, box._segment.create_id)


def test_follow_without_a_field_reports_changes_down_a_chain_with_their_path(
    names: Callable[[str], str],
) -> None:
    """Check that follow() emits each change inside the boxes down a chain on nested with its path, and moves when a reference below the outer box changes."""
    seen: queue.Queue[tuple[tuple[str, ...], object, object]] = queue.Queue()
    with (
        Encoder.create(names("e1")) as e1,
        Encoder.create(names("e2")) as e2,
        Motor.create(names("m"), 0, e1) as motor,
        Stage.create(names("s"), 0, motor) as stage,
    ):
        assert stage.events.follow() is None
        stage.events.nested.connect(lambda path, new, old: seen.put((path, new, old)))
        e1.count = 3
        assert seen.get(timeout=5) == (("motor", "encoder", "count"), 3, 0)
        motor.position = 7
        assert seen.get(timeout=5) == (("motor", "position"), 7, 0)
        stage.target = 1
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)
        motor.encoder = e2
        assert seen.get(timeout=5) == (("motor", "encoder"), ref_of(e2), ref_of(e1))
        settle(lambda value: setattr(e2, "count", value), seen)
        e1.count = -1
        e2.count = 200
        path, new, _ = seen.get(timeout=5)
        assert (path, new) == (("motor", "encoder", "count"), 200)
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)


def follow_loops(a: Node, b: Node, c: Node, out: "Queue[object]") -> None:
    seen: queue.Queue[tuple[tuple[str, ...], object]] = queue.Queue()
    with a, b, c:
        for box in (a, c):
            box.events.follow()
            box.events.nested.connect(lambda path, new, old: seen.put((path, new)))
        b.value = 20
        out.put(seen.get(timeout=5))
        a.value = 10
        c.value = 30
        time.sleep(0.3)
        out.put(seen.qsize())
        out.put(threads(a, b, c))


def test_follow_without_a_field_ends_at_a_box_it_already_follows(
    names: Callable[[str], str],
) -> None:
    """Check that follow() follows no box twice, so a loop of two boxes and a box that refers to itself end."""
    ctx = mp.get_context("spawn")
    out: Queue[object] = ctx.Queue()
    with (
        Node.create(names("a"), 1) as a,
        Node.create(names("b"), 2) as b,
        Node.create(names("c"), 3) as c,
    ):
        a.link = b
        b.link = a
        c.link = c
        # In a child, so a follow() that never ends is killed instead of hanging the run.
        child = ctx.Process(target=follow_loops, args=(a, b, c, out))
        child.start()
        child.join(30)
        if child.is_alive():
            child.kill()
            child.join()
        assert child.exitcode == 0
        assert out.get(timeout=5) == (("link", "value"), 20)
        assert out.get(timeout=5) == 0
        # One watcher each for the events of a and c, and one forwarding handle on b.
        assert out.get(timeout=5) == [1, 1, 1]


def test_follow_without_a_field_reports_a_box_two_fields_share_under_the_first(
    names: Callable[[str], str],
) -> None:
    """Check that a box two reference fields share is followed once, under the first field, as snapshot(follow=True) reads it."""
    seen: queue.Queue[tuple[tuple[str, ...], object]] = queue.Queue()
    with (
        Motor.create(names("m")) as motor,
        Pair.create(names("p"), motor, motor) as pair,
    ):
        pair.events.follow()
        pair.events.nested.connect(lambda path, new, old: seen.put((path, new)))
        motor.position = 4
        assert seen.get(timeout=5) == (("first", "position"), 4)
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)
        assert threads(motor) == [1]


def test_follow_without_a_field_reports_every_field_of_a_subclass_box(
    names: Callable[[str], str],
) -> None:
    """Check that follow() reports the fields only a subclass box has, which follow(field) leaves out."""
    seen: queue.Queue[tuple[tuple[str, ...], object]] = queue.Queue()
    with (
        GearMotor.create(names("g")) as geared,
        Stage.create(names("s"), 0, geared) as stage,
    ):
        stage.events.follow()
        stage.events.nested.connect(lambda path, new, old: seen.put((path, new)))
        geared.gear = 3
        assert seen.get(timeout=5) == (("motor", "gear"), 3)


def test_unfollow_and_close_release_what_follow_without_a_field_took(
    names: Callable[[str], str],
) -> None:
    """Check that unfollow() and close() give back the threads and waiter slots follow() took down a chain."""
    with (
        Encoder.create(names("e")) as encoder,
        Motor.create(names("m"), 0, encoder) as motor,
    ):
        stage = Stage.create(names("s"), 0, motor)
        try:
            stage.events.follow()
            assert until(lambda: waiters(motor, encoder) == [1, 1])
            assert threads(motor, encoder) == [1, 1]
            stage.events.unfollow()
            assert (threads(motor, encoder), waiters(motor, encoder)) == (
                [0, 0],
                [0, 0],
            )
            stage.events.follow()
            stage.events.follow("motor")
            assert until(lambda: waiters(motor, encoder) == [2, 1])
            stage.close()
            assert (threads(motor, encoder), waiters(motor, encoder)) == (
                [0, 0],
                [0, 0],
            )
        finally:
            stage.close()


def test_following_and_unfollowing_while_the_reference_moves_leaves_nothing(
    names: Callable[[str], str], caplog: pytest.LogCaptureFixture
) -> None:
    """Check that follow and unfollow racing with reassignments from another thread leave no forwarding thread or waiter slot behind, and log no error."""
    stop = threading.Event()
    with (
        Motor.create(names("a")) as a,
        Motor.create(names("b")) as b,
        Stage.create(names("s"), 0, a) as stage,
        caplog.at_level(logging.ERROR, logger="sharedbox"),
    ):

        def reassign() -> None:
            turn = 0
            while not stop.is_set():
                turn += 1
                stage.motor = b if turn % 2 else a
                time.sleep(0.001)

        mover = threading.Thread(target=reassign)
        mover.start()
        try:
            for _ in range(100):
                stage.events.follow("motor")
                stage.events.follow()
                stage.events.unfollow()
        finally:
            stop.set()
            mover.join(10)
        assert until(lambda: threads(a, b) == [0, 0])
        assert until(lambda: waiters(a, b) == [0, 0])
    assert not caplog.records


def test_a_move_while_follow_attaches_the_boxes_below_waits_and_follows_the_new_chain(
    names: Callable[[str], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check that a move made while follow() is between attaching a box and the box below it waits for follow(), then follows the new chain and nothing of the old one."""
    seen: queue.Queue[tuple[tuple[str, ...], object]] = queue.Queue()
    signalled = threading.Event()
    with (
        Encoder.create(names("e")) as encoder,
        Motor.create(names("a"), 0, encoder) as a,
        Motor.create(names("b"), 0, encoder) as b,
        Stage.create(names("s"), 0, a) as stage,
    ):

        def move_first(spec: Any, create_id: int, schema_hash: int, name: str) -> Any:
            if name == encoder.name and not signalled.is_set():
                stage.motor = b
                assert signalled.wait(5)
                # Time for a move that does not wait for follow() to close the handle on a.
                until(lambda: threads(a) == [0], seconds=1)
            return attach_reference(spec, create_id, schema_hash, name)

        stage.events.motor.connect(lambda new, old: signalled.set())
        stage.events.nested.connect(lambda path, new, old: seen.put((path, new)))
        # No public call pauses follow() after it attached a and before the encoder below a.
        monkeypatch.setattr("sharedbox._follow.attach_reference", move_first)
        stage.events.follow()
        assert signalled.is_set()
        assert until(lambda: threads(a, b, encoder) == [0, 1, 1])
        assert until(lambda: waiters(a, b, encoder) == [0, 1, 1])
        encoder.count = 3
        assert seen.get(timeout=5) == (("motor", "encoder", "count"), 3)
        a.position = 5
        b.position = 6
        assert seen.get(timeout=5) == (("motor", "position"), 6)
        with pytest.raises(queue.Empty):
            seen.get(timeout=0.3)


def test_a_follow_that_raises_gives_back_what_it_took_and_is_tried_again(
    names: Callable[[str], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check that follow() and follow(field) that fail to attach a box keep no thread or waiter slot, and a later call follows the box."""
    seen: queue.Queue[object] = queue.Queue()
    with (
        Encoder.create(names("e")) as encoder,
        Motor.create(names("m"), 0, encoder) as motor,
        Stage.create(names("s"), 0, motor) as stage,
    ):

        def fail_below(spec: Any, create_id: int, schema_hash: int, name: str) -> Any:
            if name == encoder.name:
                raise OSError("too many open files")
            return attach_reference(spec, create_id, schema_hash, name)

        # No public input makes attaching the encoder fail with an error follow() raises.
        monkeypatch.setattr("sharedbox._follow.attach_reference", fail_below)
        with pytest.raises(OSError, match="too many open files"):
            stage.events.follow()
        assert (threads(motor, encoder), waiters(motor, encoder)) == ([0, 0], [0, 0])
        motor_events = stage.events.follow("motor")
        with pytest.raises(OSError, match="too many open files"):
            motor_events.follow("encoder")
        monkeypatch.undo()
        stage.events.follow()
        stage.events.nested.connect(lambda path, new, old: seen.put((path, new)))
        motor_events.follow("encoder").count.connect(lambda new, old: seen.put(new))
        assert until(lambda: waiters(motor, encoder) == [2, 2])
        encoder.count = 1
        got = {seen.get(timeout=5), seen.get(timeout=5)}
        assert got == {(("motor", "encoder", "count"), 1), 1}
