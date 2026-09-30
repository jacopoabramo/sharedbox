import errno
import multiprocessing as mp
import os
import queue
import sys
import threading
from collections.abc import Callable
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event

import pytest

from sharedbox import BoxEvents, LockTimeoutError, SharedBox
from sharedbox._layout import NativeField
from sharedbox._native import Segment

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="Windows has no fork"),
    pytest.mark.filterwarnings("ignore:This process .* is multi-threaded"),
]


INT = 1
NOBODY = 65534
ROOT = sys.platform != "win32" and os.geteuid() == 0


class Counter(SharedBox):
    value: int = 0


class Wheel(SharedBox):
    turns: int = 0


class Cart(SharedBox):
    wheel: Wheel | None = None


def read_wheel(cart: Cart, out: "Queue[str]") -> None:
    wheel = cart.wheel
    assert wheel is not None
    out.put(wheel.name)
    cart.close()


def forward_in_child(cart: Cart, ready: Event, out: "Queue[int]") -> None:
    seen: queue.Queue[int] = queue.Queue()
    cart.events.follow("wheel").turns.connect(lambda new, old: seen.put(new))
    ready.set()
    out.put(seen.get(timeout=10))
    cart.close()


def follow_kept_group(
    cart: Cart, group: BoxEvents, ready: Event, out: "Queue[int]"
) -> None:
    seen: queue.Queue[int] = queue.Queue()
    group.follow("wheel").turns.connect(lambda new, old: seen.put(new))
    ready.set()
    out.put(seen.get(timeout=10))
    cart.close()


def close_box(box: Counter) -> None:
    box.close()


def hold_write_lock(segment: Segment) -> None:
    segment._hold_write_lock()


def watch_box(box: Counter, ready: Event, out: "Queue[int]") -> None:
    values = iter(box.watch("value"))
    ready.set()
    out.put(next(values))
    box.close()


def unlink_as_another_user(name: str, out: "Queue[object]") -> None:
    if sys.platform != "win32":
        os.setuid(NOBODY)
    try:
        Counter.unlink(name)
    except OSError as error:
        out.put((type(error).__name__, error.errno))
    else:
        out.put(None)


def busy_box(name: str) -> Counter:
    """A box whose watcher thread is waiting inside the segment, with events connected."""
    box = Counter.create(name)
    box.events.value.connect(lambda new: None)
    changes = iter(box.watch("value"))
    seen: queue.Queue[int] = queue.Queue()
    threading.Thread(target=lambda: seen.put(next(changes)), daemon=True).start()
    box.value = 1
    seen.get(timeout=5)
    return box


def fork(target: Callable[..., object], *args: object) -> BaseProcess:
    if sys.platform == "win32":
        raise NotImplementedError("Windows has no fork")
    else:
        process = mp.get_context("fork").Process(target=target, args=args, daemon=True)
        process.start()
        return process


def finish(process: BaseProcess) -> int | None:
    process.join(timeout=20)
    if process.is_alive():
        process.kill()
        process.join()
        return None
    return process.exitcode


def test_forked_child_closes_an_inherited_box(unique_name: str) -> None:
    """Check that a forked child can close a box inherited from its parent."""
    with busy_box(unique_name) as box:
        child = fork(close_box, box)
        assert finish(child) == 0


def test_forked_child_watches_an_inherited_box(unique_name: str) -> None:
    """Check that a forked child watching an inherited box sees a write made by the parent."""
    ctx = mp.get_context("fork")
    ready = ctx.Event()
    out: Queue[int] = ctx.Queue()
    with busy_box(unique_name) as box:
        child = fork(watch_box, box, ready, out)
        assert ready.wait(10)
        box.value = 9
        assert out.get(timeout=10) == 9
        assert finish(child) == 0


@pytest.mark.skipif(not ROOT, reason="needs root to switch to another user")
def test_unlink_reports_a_refused_unlink_as_oserror(unique_name: str) -> None:
    """Check that unlink by a user without permission raises PermissionError with EACCES or EPERM."""
    out: Queue[object] = mp.get_context("fork").Queue()
    with Counter.create(unique_name):
        child = fork(unlink_as_another_user, unique_name, out)
        assert out.get(timeout=10) in [
            ("PermissionError", errno.EACCES),
            ("PermissionError", errno.EPERM),
        ]
        assert finish(child) == 0
    Counter.unlink(unique_name)


def test_forked_child_records_its_own_pid_in_a_raw_segment(unique_name: str) -> None:
    """Check that a lock held by a forked child names the child's pid in the timeout error."""
    segment = Segment.create(
        unique_name, [NativeField(0, 8, INT)], ["a"], 8, 1, 0.3, []
    )
    child = fork(hold_write_lock, segment)
    assert finish(child) == 0
    with pytest.raises(LockTimeoutError, match=rf"locked by pid {child.pid}\b"):
        segment._write([(0, bytes(8))])
    segment.force_unlock()
    segment.close()


def test_forked_child_reads_a_reference_while_the_parent_held_the_cache_lock(
    unique_name: str,
) -> None:
    """Check that a forked child reads a reference although the parent held the reference cache lock at the fork."""
    out: Queue[str] = mp.get_context("fork").Queue()
    wheel_name = f"{unique_name}-w"
    with Wheel.create(wheel_name) as wheel, Cart.create(unique_name, wheel) as cart:
        # Stands for another thread of the parent attaching a box at the moment of the fork.
        with cart._refs_lock:
            child = fork(read_wheel, cart, out)
        assert out.get(timeout=10) == wheel_name
        assert finish(child) == 0
    Wheel.unlink(wheel_name)


def test_forked_child_forwards_after_calling_follow(unique_name: str) -> None:
    """Check that a forked child forwards changes once it calls follow, although the parent held the forwarding lock at the fork."""
    ctx = mp.get_context("fork")
    ready = ctx.Event()
    out: Queue[int] = ctx.Queue()
    wheel_name = f"{unique_name}-w"
    with Wheel.create(wheel_name) as wheel, Cart.create(unique_name, wheel) as cart:
        cart.events.follow("wheel")
        # Stands for another thread of the parent moving forwarding at the moment of the fork.
        with cart.events._sharedbox_follower.lock:
            child = fork(forward_in_child, cart, ready, out)
        assert ready.wait(10)
        wheel.turns = 4
        assert out.get(timeout=10) == 4
        assert finish(child) == 0
    Wheel.unlink(wheel_name)


def test_forked_child_follows_a_move_through_a_group_kept_from_before_the_fork(
    unique_name: str,
) -> None:
    """Check that a forked child calling follow on a group kept from before the fork forwards from the box its reference moves to."""
    ctx = mp.get_context("fork")
    ready = ctx.Event()
    out: Queue[int] = ctx.Queue()
    first, second = f"{unique_name}-w", f"{unique_name}-v"
    with Wheel.create(first) as wheel, Cart.create(unique_name, wheel) as cart:
        group = cart.events
        group.follow("wheel")
        child = fork(follow_kept_group, cart, group, ready, out)
        assert ready.wait(10)
        with Wheel.create(second) as moved:
            cart.wheel = moved
            # The child follows the new box some time after the move and misses writes made before that.
            got = None
            for turns in range(1, 101):
                moved.turns = turns
                try:
                    got = out.get(timeout=0.1)
                except queue.Empty:
                    continue
                break
            assert got is not None
        assert finish(child) == 0
    Wheel.unlink(first)
    Wheel.unlink(second)
