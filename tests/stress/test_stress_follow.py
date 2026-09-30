import contextlib
import queue
import threading
import time
from collections.abc import Callable, Sequence
from functools import partial
from typing import Any, cast

import pytest
from stress_helpers import percentiles, scaled

from sharedbox import SharedBox

pytestmark = pytest.mark.stress

IDLE = 2.0
WIDTHS = (1, 16, 64)
DEPTHS = (1, 2, 3)


class Link(SharedBox):
    value: int = 0
    link: "Link | None" = None


def wide_class(width: int) -> type[SharedBox]:
    """A class with `width` reference fields `r0`, `r1`, ..., each a `Link` or None."""
    namespace: dict[str, Any] = {
        "__annotations__": {f"r{i}": Link | None for i in range(width)}
    }
    namespace.update({f"r{i}": None for i in range(width)})
    return cast(type[SharedBox], type(f"Wide{width}", (SharedBox,), namespace))


WIDE = {width: wide_class(width) for width in WIDTHS}


def until(condition: Callable[[], bool], seconds: float) -> bool:
    """Whether `condition` held within `seconds`, checked every 0.2 ms."""
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.0002)
    return True


def chain(stack: contextlib.ExitStack, name: str, depth: int) -> list[Link]:
    """`depth` boxes, each referring to the next, closed and removed when `stack` ends."""
    boxes: list[Link] = []
    below = None
    for level in reversed(range(depth)):
        stack.callback(Link.unlink, f"{name}-{level}")
        below = stack.enter_context(Link.create(f"{name}-{level}", 0, below))
        boxes.insert(0, below)
    return boxes


def slots(boxes: list[Link]) -> int:
    """Waiter slots held in `boxes`, by every handle on them."""
    # The waiter count is not public; it is how the test sees the slots forwarding holds.
    return sum(box._segment._waiters for box in boxes)


def watchers(boxes: Sequence[SharedBox]) -> int:
    """Watcher threads of this process for `boxes`."""
    names = {f"sharedbox-watch-{box.name}" for box in boxes}
    return sum(thread.name in names for thread in threading.enumerate())


def moved_to(new: list[Link], old: list[Link]) -> bool:
    """Whether forwarding holds a slot in every box of `new` and none in `old`."""
    return slots(new) == len(new) and slots(old) == 0


def follow_round(
    name: str, width: int, depth: int, writes: int, moves: int
) -> dict[str, Any]:
    """Follow `width` chains of `depth` boxes with one `follow()` and measure it.

    Writes go to the deepest box of each chain in turn; each move assigns
    the first field a spare chain, then the first chain again.
    """
    with contextlib.ExitStack() as stack:
        chains = [chain(stack, f"{name}-{i}", depth) for i in range(width)]
        spare = chain(stack, f"{name}-spare", depth)
        followed = [box for boxes in chains for box in boxes]
        stack.callback(WIDE[width].unlink, f"{name}-w")
        outer = stack.enter_context(
            WIDE[width].create(
                f"{name}-w", **{f"r{i}": boxes[0] for i, boxes in enumerate(chains)}
            )
        )
        seen: queue.Queue[tuple[int, int]] = queue.Queue()
        outer.events.nested.connect(
            lambda path, new, old: seen.put((new, time.perf_counter_ns()))
        )
        outer.events.follow()
        registered = until(lambda: slots(followed) == width * depth, 30)
        threads = watchers(followed)
        slots_in_use = slots(followed)
        idle_start = time.process_time()
        time.sleep(IDLE)
        idle_cpu = time.process_time() - idle_start
        latency: list[int] = []
        for n in range(1, writes + 1):
            deepest = chains[n % width][-1]
            start = time.perf_counter_ns()
            deepest.value = n
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    value, when = seen.get(timeout=0.5)
                except queue.Empty:
                    continue
                if value == n:
                    latency.append(when - start)
                    break
        move: list[int] = []
        for k in range(moves):
            new, old = (spare, chains[0]) if k % 2 == 0 else (chains[0], spare)
            start = time.perf_counter_ns()
            outer.update(r0=new[0])
            if until(partial(moved_to, new, old), 5):
                move.append(time.perf_counter_ns() - start)
        outer.close()
        slots_after_close = slots(followed + spare)
        threads_after_close = watchers([*followed, *spare, outer])
    return {
        "followed_boxes": width * depth,
        "registered": registered,
        "threads": threads,
        "waiter_slots": slots_in_use,
        "idle_cpu_s_per_box_per_s": idle_cpu / (width * depth) / IDLE,
        "idle_cpu_s": idle_cpu,
        "delivered": len(latency),
        "writes": writes,
        "write_to_callback_us": percentiles(latency),
        "moves": moves,
        "moved": len(move),
        "move_us": percentiles(move),
        "waiter_slots_after_close": slots_after_close,
        "threads_after_close": threads_after_close,
    }


def test_following_costs_one_thread_and_one_slot_per_box(
    unique_name: str, report: Callable[[dict[str, object]], None]
) -> None:
    """Check that follow() takes one thread and one slot per box, delivers every write and move, and frees all on close."""
    writes, moves = scaled(200), scaled(20)
    rounds = {
        f"{width}x{depth}": follow_round(
            f"{unique_name}-{width}x{depth}", width, depth, writes, moves
        )
        for width in WIDTHS
        for depth in DEPTHS
    }
    report(dict(rounds))
    for key, r in rounds.items():
        boxes = r["followed_boxes"]
        assert r["registered"], key
        assert r["threads"] == boxes, key
        assert r["waiter_slots"] == boxes, key
        assert r["delivered"] == writes, key
        assert r["moved"] == moves, key
        # An idle watcher wakes once a second; a busy loop would take a whole core.
        assert r["idle_cpu_s"] < 0.01 * boxes * IDLE + 0.1, key
        assert r["waiter_slots_after_close"] == 0, key
        assert r["threads_after_close"] == 0, key
