"""Round trips between two processes: the parent sends a counter, the child answers.

The first round trips are discarded as warm-up. The polling contender keeps
one CPU core busy on each side.

    python -m sharedbox.benchmarks.roundtrip
    python -m sharedbox.benchmarks.roundtrip --samples 1000 --timeout 5
"""

import argparse
import multiprocessing as mp
import os
import statistics
import struct
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnContext, SpawnProcess
from multiprocessing.shared_memory import SharedMemory
from multiprocessing.synchronize import Event
from typing import TypedDict

from sharedbox import SharedBox

WARMUP = 200
SAMPLES = 5000
TIMEOUT = 10.0
INT = struct.Struct("<q")


@dataclass(frozen=True)
class Options:
    """How many round trips to time, and how long to wait for one answer."""

    samples: int = SAMPLES
    warmup: int = WARMUP
    timeout: float = TIMEOUT

    @property
    def total(self) -> int:
        return self.warmup + self.samples


class Result(TypedDict):
    contender: str
    p50_us: float
    p90_us: float
    p99_us: float
    max_us: float
    samples: int


class PingPong(SharedBox):
    ping: int = 0
    pong: int = 0


def measure(
    label: str,
    send: Callable[[int], object],
    receive: Callable[[int], bool],
    opts: Options,
) -> list[int]:
    """Nanoseconds per round trip, after the warm-up; exits if an answer is late."""
    samples = []
    for i in range(1, opts.total + 1):
        start = time.perf_counter_ns()
        send(i)
        answered = receive(i)
        samples.append(time.perf_counter_ns() - start)
        if not answered:
            raise SystemExit(
                f"{label}: no answer to round trip {i} within {opts.timeout} s; "
                "a busy machine may need a longer --timeout"
            )
    return samples[opts.warmup :]


def run_child(
    ctx: SpawnContext, target: Callable[..., None], *args: object
) -> SpawnProcess:
    child = ctx.Process(target=target, args=args, daemon=True)
    child.start()
    return child


def stop_child(child: SpawnProcess, timeout: float) -> None:
    child.join(timeout)
    if child.is_alive():
        child.terminate()


def box_child(name: str, total: int) -> None:
    box = PingPong.attach(name)
    pings = box.watch("ping")
    box.pong = -1
    for value in pings:
        box.pong = value
        if value == total:
            break
    box.close()


def box_round_trips(ctx: SpawnContext, opts: Options) -> list[int]:
    label = "SharedBox.watch()"
    name = f"bench-roundtrip-{os.getpid()}"
    box = PingPong.create(name)
    sent = [0]
    done = threading.Event()

    # watch() has no timeout, so closing the box is what ends a stuck wait.
    def watchdog() -> None:
        last = -1
        while not done.wait(opts.timeout):
            if sent[0] == last:
                box.close()
                return
            last = sent[0]

    def send(i: int) -> None:
        sent[0] = i
        box.ping = i

    child: SpawnProcess | None = None
    try:
        pongs: Iterator[int] = iter(box.watch("pong"))
        child = run_child(ctx, box_child, name, opts.total)
        threading.Thread(target=watchdog, daemon=True).start()
        if next(pongs, None) != -1:
            raise SystemExit(f"{label}: the child did not start")
        return measure(label, send, lambda i: next(pongs, None) == i, opts)
    finally:
        done.set()
        if child is not None:
            stop_child(child, opts.timeout)
        box.close()
        PingPong.unlink(name)


def event_child(ping: Event, pong: Event, total: int, timeout: float) -> None:
    pong.set()
    for _ in range(total):
        if not ping.wait(timeout):
            return
        ping.clear()
        pong.set()


def event_round_trips(ctx: SpawnContext, opts: Options) -> list[int]:
    label = "mp.Event"
    ping, pong = ctx.Event(), ctx.Event()
    child = run_child(ctx, event_child, ping, pong, opts.total, opts.timeout)

    def receive(_: int) -> bool:
        answered = pong.wait(opts.timeout)
        pong.clear()
        return answered

    try:
        if not pong.wait(opts.timeout):
            raise SystemExit(f"{label}: the child did not start")
        pong.clear()
        return measure(label, lambda _: ping.set(), receive, opts)
    finally:
        stop_child(child, opts.timeout)


def pipe_child(conn: Connection, total: int, timeout: float) -> None:
    conn.send(-1)
    for _ in range(total):
        if not conn.poll(timeout):
            return
        conn.send(conn.recv())


def pipe_round_trips(ctx: SpawnContext, opts: Options) -> list[int]:
    label = "mp.Pipe"
    conn, child_conn = ctx.Pipe()
    child = run_child(ctx, pipe_child, child_conn, opts.total, opts.timeout)
    try:
        if not (conn.poll(opts.timeout) and conn.recv() == -1):
            raise SystemExit(f"{label}: the child did not start")
        return measure(
            label,
            conn.send,
            lambda i: conn.poll(opts.timeout) and conn.recv() == i,
            opts,
        )
    finally:
        stop_child(child, opts.timeout)


def buffer(shm: SharedMemory) -> memoryview:
    if shm.buf is None:
        raise RuntimeError(f"shared memory {shm.name} is closed")
    return shm.buf


def poll_child(name: str, total: int) -> None:
    shm = SharedMemory(name)
    buf = buffer(shm)
    INT.pack_into(buf, 8, -1)
    last = 0
    while last < total:
        value = INT.unpack_from(buf, 0)[0]
        if value != last:
            INT.pack_into(buf, 8, value)
            last = value
    shm.close()


def poll_until(buf: memoryview, expected: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while INT.unpack_from(buf, 8)[0] != expected:
        if time.monotonic() > deadline:
            return False
    return True


def poll_round_trips(ctx: SpawnContext, opts: Options) -> list[int]:
    label = "SharedMemory polling"
    shm = SharedMemory(f"bench-roundtrip-{os.getpid()}", create=True, size=16)
    buf = buffer(shm)
    child: SpawnProcess | None = None
    try:
        child = run_child(ctx, poll_child, shm.name, opts.total)
        if not poll_until(buf, -1, opts.timeout):
            raise SystemExit(f"{label}: the child did not start")
        return measure(
            label,
            lambda i: INT.pack_into(buf, 0, i),
            lambda i: poll_until(buf, i, opts.timeout),
            opts,
        )
    finally:
        if child is not None:
            stop_child(child, opts.timeout)
        shm.close()
        shm.unlink()


CONTENDERS: list[tuple[str, Callable[[SpawnContext, Options], list[int]]]] = [
    ("SharedBox.watch()", box_round_trips),
    ("mp.Event", event_round_trips),
    ("mp.Pipe", pipe_round_trips),
    ("SharedMemory polling (busy CPU)", poll_round_trips),
]


def run(opts: Options) -> list[Result]:
    """Round-trip percentiles of every contender, in microseconds."""
    ctx = mp.get_context("spawn")
    results = []
    for label, round_trips in CONTENDERS:
        samples = round_trips(ctx, opts)
        cuts = statistics.quantiles(samples, n=100)
        results.append(
            Result(
                contender=label,
                p50_us=cuts[49] / 1000,
                p90_us=cuts[89] / 1000,
                p99_us=cuts[98] / 1000,
                max_us=max(samples) / 1000,
                samples=len(samples),
            )
        )
    return results


def to_text(results: list[Result]) -> str:
    lines = [
        f"{'contender':32} {'p50':>8} {'p90':>8} {'p99':>8} {'max':>9} samples",
        f"{'':32} {'us':>8} {'us':>8} {'us':>8} {'us':>9}",
    ]
    lines += [
        f"{r['contender']:32} {r['p50_us']:8.1f} {r['p90_us']:8.1f} "
        f"{r['p99_us']:8.1f} {r['max_us']:9.1f} {r['samples']}"
        for r in results
    ]
    return "\n".join(lines)


def to_markdown(results: list[Result]) -> str:
    lines = [
        "| contender | p50 (us) | p90 (us) | p99 (us) | max (us) | samples |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {r['contender']} | {r['p50_us']:.1f} | {r['p90_us']:.1f} "
        f"| {r['p99_us']:.1f} | {r['max_us']:.1f} | {r['samples']} |"
        for r in results
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=SAMPLES)
    parser.add_argument("--warmup", type=int, default=WARMUP)
    parser.add_argument("--timeout", type=float, default=TIMEOUT)
    args = parser.parse_args(argv)
    print(to_text(run(Options(args.samples, args.warmup, args.timeout))))


if __name__ == "__main__":
    main()
