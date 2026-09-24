"""Round trips between two processes: the parent sends a counter, the child answers.

The first round trips are discarded as warm-up. The polling contender keeps
one CPU core busy on each side.

    uv run python benchmarks/bench_roundtrip.py
"""

import multiprocessing as mp
import os
import statistics
import struct
import threading
import time
from collections.abc import Callable, Iterator
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnContext
from multiprocessing.shared_memory import SharedMemory
from multiprocessing.synchronize import Event

from sharedbox import SharedBox

WARMUP = 200
SAMPLES = 5000
TOTAL = WARMUP + SAMPLES
TIMEOUT = 10.0
INT = struct.Struct("<q")


class PingPong(SharedBox):
    ping: int = 0
    pong: int = 0


def measure(
    label: str, send: Callable[[int], object], receive: Callable[[int], bool]
) -> list[int]:
    """Nanoseconds per round trip, after the warm-up; exits if an answer is late."""
    samples = []
    for i in range(1, TOTAL + 1):
        start = time.perf_counter_ns()
        send(i)
        answered = receive(i)
        samples.append(time.perf_counter_ns() - start)
        if not answered:
            raise SystemExit(f"{label}: no answer to round trip {i} within {TIMEOUT} s")
    return samples[WARMUP:]


def run_child(
    ctx: SpawnContext, target: Callable[..., None], *args: object
) -> mp.Process:
    child = ctx.Process(target=target, args=args, daemon=True)
    child.start()
    return child


def stop_child(child: mp.Process) -> None:
    child.join(TIMEOUT)
    if child.is_alive():
        child.terminate()


def box_child(name: str) -> None:
    box = PingPong.attach(name)
    pings = box.watch("ping")
    box.pong = -1
    for value in pings:
        box.pong = value
        if value == TOTAL:
            break
    box.close()


def box_round_trips(ctx: SpawnContext) -> list[int]:
    label = "SharedBox.watch()"
    name = f"bench-roundtrip-{os.getpid()}"
    box = PingPong.create(name)
    # watch() has no timeout, so closing the box is what ends a stuck wait.
    watchdog = threading.Timer(TIMEOUT * 6, box.close)
    try:
        pongs: Iterator[int] = iter(box.watch("pong"))
        child = run_child(ctx, box_child, name)
        watchdog.start()
        if next(pongs, None) != -1:
            raise SystemExit(f"{label}: the child did not start")
        samples = measure(
            label,
            lambda i: setattr(box, "ping", i),
            lambda i: next(pongs, None) == i,
        )
        stop_child(child)
        return samples
    finally:
        watchdog.cancel()
        box.close()
        PingPong.unlink(name)


def event_child(ping: Event, pong: Event) -> None:
    pong.set()
    for _ in range(TOTAL):
        if not ping.wait(TIMEOUT):
            return
        ping.clear()
        pong.set()


def event_round_trips(ctx: SpawnContext) -> list[int]:
    label = "mp.Event"
    ping, pong = ctx.Event(), ctx.Event()
    child = run_child(ctx, event_child, ping, pong)
    if not pong.wait(TIMEOUT):
        raise SystemExit(f"{label}: the child did not start")
    pong.clear()

    def receive(_: int) -> bool:
        answered = pong.wait(TIMEOUT)
        pong.clear()
        return answered

    samples = measure(label, lambda _: ping.set(), receive)
    stop_child(child)
    return samples


def pipe_child(conn: Connection) -> None:
    conn.send(-1)
    for _ in range(TOTAL):
        if not conn.poll(TIMEOUT):
            return
        conn.send(conn.recv())


def pipe_round_trips(ctx: SpawnContext) -> list[int]:
    label = "mp.Pipe"
    conn, child_conn = ctx.Pipe()
    child = run_child(ctx, pipe_child, child_conn)
    if not (conn.poll(TIMEOUT) and conn.recv() == -1):
        raise SystemExit(f"{label}: the child did not start")
    samples = measure(
        label, conn.send, lambda i: conn.poll(TIMEOUT) and conn.recv() == i
    )
    stop_child(child)
    return samples


def poll_child(name: str) -> None:
    shm = SharedMemory(name)
    INT.pack_into(shm.buf, 8, -1)
    last = 0
    while last < TOTAL:
        value = INT.unpack_from(shm.buf, 0)[0]
        if value != last:
            INT.pack_into(shm.buf, 8, value)
            last = value
    shm.close()


def poll_until(shm: SharedMemory, expected: int) -> bool:
    deadline = time.monotonic() + TIMEOUT
    while INT.unpack_from(shm.buf, 8)[0] != expected:
        if time.monotonic() > deadline:
            return False
    return True


def poll_round_trips(ctx: SpawnContext) -> list[int]:
    label = "SharedMemory polling"
    shm = SharedMemory(f"bench-roundtrip-{os.getpid()}", create=True, size=16)
    try:
        child = run_child(ctx, poll_child, shm.name)
        if not poll_until(shm, -1):
            raise SystemExit(f"{label}: the child did not start")
        samples = measure(
            label,
            lambda i: INT.pack_into(shm.buf, 0, i),
            lambda i: poll_until(shm, i),
        )
        stop_child(child)
        return samples
    finally:
        shm.close()
        shm.unlink()


CONTENDERS: list[tuple[str, Callable[[SpawnContext], list[int]]]] = [
    ("SharedBox.watch()", box_round_trips),
    ("mp.Event", event_round_trips),
    ("mp.Pipe", pipe_round_trips),
    ("SharedMemory polling (busy CPU)", poll_round_trips),
]


def main() -> None:
    ctx = mp.get_context("spawn")
    print(f"{'contender':32} {'p50':>8} {'p90':>8} {'p99':>8} {'max':>9} samples")
    print(f"{'':32} {'us':>8} {'us':>8} {'us':>8} {'us':>9}")
    for label, round_trips in CONTENDERS:
        samples = round_trips(ctx)
        cuts = statistics.quantiles(samples, n=100)
        p50, p90, p99 = (cuts[i] / 1000 for i in (49, 89, 98))
        print(
            f"{label:32} {p50:8.1f} {p90:8.1f} {p99:8.1f} "
            f"{max(samples) / 1000:9.1f} {len(samples)}"
        )


if __name__ == "__main__":
    main()
