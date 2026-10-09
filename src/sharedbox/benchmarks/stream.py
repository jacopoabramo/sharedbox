"""Stream throughput and the sync/async matrix, in spawned processes.

benchbox stream
python -m sharedbox.benchmarks.stream --short --json stream.json
"""

import argparse
import json
import multiprocessing as mp
import os
import queue
import statistics
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import count
from multiprocessing.context import SpawnContext, SpawnProcess
from multiprocessing.queues import Queue
from multiprocessing.shared_memory import SharedMemory
from multiprocessing.synchronize import Barrier, Condition
from pathlib import Path
from threading import BrokenBarrierError
from typing import Annotated, Any, TypedDict

import numpy as np

from sharedbox import DType, Shape, SharedStream
from sharedbox._stream import Mode

NAMES = count()
Report = tuple[str, int, float, int]
Spec = tuple[Callable[..., None], tuple[Any, ...]]


@dataclass(frozen=True)
class Small:
    stamp: int
    data: Annotated[np.ndarray, Shape(1024), DType("uint8")]


@dataclass(frozen=True)
class Frame:
    stamp: int
    data: Annotated[np.ndarray, Shape(512, 512), DType("uint16")]


SIZES = {"1 KiB": Small, "512 KiB": Frame}


@dataclass(frozen=True)
class Options:
    """How many items each run sends, how often each is repeated and how long to wait."""

    items: dict[str, int] = field(
        default_factory=lambda: {"1 KiB": 20000, "512 KiB": 2000}
    )
    readers: tuple[int, ...] = (1, 4)
    capacity: int = 32
    latency_items: int = 500
    repeats: int = 3
    timeout: float = 30.0

    @classmethod
    def short(cls) -> "Options":
        """A run of a few seconds, for tests and CI."""
        return cls({"1 KiB": 2000, "512 KiB": 200}, (1, 4), 32, 100, 1, 30.0)


class Throughput(TypedDict):
    contender: str
    item: str
    readers: int
    sent_per_s: float
    received_per_s: float
    missed: int


def zeros(item: str) -> np.ndarray:
    """An array of the size `item` names."""
    if item == "1 KiB":
        return np.zeros(1024, np.uint8)
    return np.zeros((512, 512), np.uint16)


def stream_reader(
    name: str, mode: Mode, item: str, ready: Barrier, start: Barrier, results: Any
) -> None:
    """Receive until the stream ends, into arrays allocated once, and report the count and time."""
    stream = SharedStream.attach(SIZES[item], name)
    reader = stream.reader(mode=mode, start="oldest")
    out = {"data": zeros(item)}
    ready.wait()
    start.wait()
    begin = time.perf_counter()
    got = 0
    try:
        for _ in reader.iter_into(out):
            got += 1
    finally:
        results.put(("reader", got, time.perf_counter() - begin, reader.missed))
        stream.close()


def stream_sender(
    name: str, item: str, total: int, start: Barrier, results: Any
) -> None:
    """Send `total` items and close the stream, reporting the sending time."""
    stream = SharedStream.attach(SIZES[item], name)
    value = SIZES[item](0, zeros(item))
    start.wait()
    begin = time.perf_counter()
    with stream.sender() as sender:
        for _ in range(total):
            sender.send(value, timeout=None)
    results.put(("sender", total, time.perf_counter() - begin, 0))
    stream.close()


def queue_reader(source: Any, ready: Barrier, start: Barrier, results: Any) -> None:
    """Take from `source` until `None` and report the count and time."""
    ready.wait()
    start.wait()
    begin = time.perf_counter()
    got = 0
    while source.get() is not None:
        got += 1
    results.put(("reader", got, time.perf_counter() - begin, 0))


def queue_sender(
    sinks: list[Any], item: str, total: int, start: Barrier, results: Any
) -> None:
    """Put `total` items on every queue, then `None`, and report the time."""
    data = zeros(item)
    value = data.tobytes() if item == "1 KiB" else data
    start.wait()
    begin = time.perf_counter()
    for _ in range(total):
        for sink in sinks:
            sink.put(value)
    for sink in sinks:
        sink.put(None)
    results.put(("sender", total, time.perf_counter() - begin, 0))


def ring_layout(item: str, capacity: int, readers: int) -> tuple[int, int]:
    """The offset of the first slot and the size of the memory, in bytes."""
    slots = (8 * (2 + readers) + 63) // 64 * 64
    return slots, slots + capacity * zeros(item).nbytes


def ring_views(
    shm: SharedMemory, item: str, capacity: int, readers: int
) -> tuple[np.ndarray, np.ndarray]:
    """The header (write index, done flag, one read index per reader) and the slots."""
    assert shm.buf is not None
    first, _ = ring_layout(item, capacity, readers)
    size = zeros(item).nbytes
    header = np.ndarray(2 + readers, np.int64, buffer=shm.buf)
    slots = np.ndarray((capacity, size), np.uint8, buffer=shm.buf, offset=first)
    return header, slots


def ring_reader(
    name: str,
    item: str,
    capacity: int,
    readers: int,
    index: int,
    cond: Condition,
    timeout: float,
    ready: Barrier,
    start: Barrier,
    results: Any,
) -> None:
    """Copy slots out under the lock until the sender is done and the ring is empty."""
    shm = SharedMemory(name)
    try:
        report = ring_take(
            shm, item, capacity, readers, index, cond, timeout, ready, start
        )
    finally:
        shm.close()
    if report is not None:
        results.put(report)


def ring_take(
    shm: SharedMemory,
    item: str,
    capacity: int,
    readers: int,
    index: int,
    cond: Condition,
    timeout: float,
    ready: Barrier,
    start: Barrier,
) -> Report | None:
    header, slots = ring_views(shm, item, capacity, readers)
    out = np.empty(slots.shape[1], np.uint8)
    me = 2 + index
    ready.wait()
    start.wait()
    begin = time.perf_counter()
    got = 0
    while True:
        with cond:
            if not cond.wait_for(lambda: header[me] < header[0] or header[1], timeout):
                return None
            if header[me] >= header[0]:
                return ("reader", got, time.perf_counter() - begin, 0)
            out[:] = slots[header[me] % capacity]
            header[me] += 1
            got += 1
            cond.notify_all()


def ring_sender(
    name: str,
    item: str,
    capacity: int,
    readers: int,
    total: int,
    cond: Condition,
    timeout: float,
    start: Barrier,
    results: Any,
) -> None:
    """Copy `total` items into the ring under the lock, waiting while it is full."""
    shm = SharedMemory(name)
    try:
        report = ring_put(shm, item, capacity, readers, total, cond, timeout, start)
    finally:
        shm.close()
    if report is not None:
        results.put(report)


def ring_put(
    shm: SharedMemory,
    item: str,
    capacity: int,
    readers: int,
    total: int,
    cond: Condition,
    timeout: float,
    start: Barrier,
) -> Report | None:
    header, slots = ring_views(shm, item, capacity, readers)
    value = zeros(item).view(np.uint8).reshape(-1)
    start.wait()
    begin = time.perf_counter()
    for _ in range(total):
        with cond:
            if not cond.wait_for(
                lambda: header[0] - header[2:].min() < capacity, timeout
            ):
                return None
            slots[header[0] % capacity] = value
            header[0] += 1
            cond.notify_all()
    with cond:
        header[1] = 1
        cond.notify_all()
    return ("sender", total, time.perf_counter() - begin, 0)


def drive(
    label: str,
    opts: Options,
    ctx: SpawnContext,
    readers: list[Spec],
    sender: Spec,
    ready: Barrier,
    start: Barrier,
    results: "Queue[Report]",
) -> list[Report]:
    """Run the readers, then the sender, release `start`, and collect every report.

    Raises
    ------
    SystemExit
        If a barrier breaks, a process is still running or one reported nothing
        within `opts.timeout` seconds, or a process exits with an error. Every
        process is terminated first.
    """
    children: list[SpawnProcess] = []
    stuck = SystemExit(f"{label}: not finished within {opts.timeout} s")
    try:
        for target, args in readers:
            children.append(
                ctx.Process(
                    target=target, args=(*args, ready, start, results), daemon=True
                )
            )
            children[-1].start()
        ready.wait(opts.timeout)
        target, args = sender
        children.append(
            ctx.Process(target=target, args=(*args, start, results), daemon=True)
        )
        children[-1].start()
        start.wait(opts.timeout)
        deadline = time.monotonic() + opts.timeout
        for child in children:
            child.join(max(0.0, deadline - time.monotonic()))
        if any(child.is_alive() for child in children):
            raise stuck
        if any(child.exitcode != 0 for child in children):
            raise SystemExit(f"{label}: a process failed")
        return [results.get(timeout=1) for _ in children]
    except (BrokenBarrierError, queue.Empty):
        raise stuck from None
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join()


def summarise(label: str, item: str, readers: int, reports: list[Report]) -> Throughput:
    """Turn the reports of one run into a row."""
    received = [got / secs for kind, got, secs, _ in reports if kind == "reader"]
    sent = next(got / secs for kind, got, secs, _ in reports if kind == "sender")
    return Throughput(
        contender=label,
        item=item,
        readers=readers,
        sent_per_s=sent,
        received_per_s=statistics.fmean(received),
        missed=sum(missed for _, _, _, missed in reports),
    )


@contextmanager
def fresh_stream(opts: Options, item: str) -> Generator[str, None, None]:
    """Create a stream, yield its name and unlink it afterwards."""
    name = f"bench-stream-{os.getpid()}-{next(NAMES)}"
    stream = SharedStream.create(
        SIZES[item], name, capacity=opts.capacity, max_readers=max(opts.readers)
    )
    try:
        yield name
    finally:
        stream.close()
        SharedStream.unlink(name)


def stream_run(mode: Mode) -> Callable[[SpawnContext, Options, str, int], Throughput]:
    """A contender that runs a stream with readers of `mode`."""

    def run(ctx: SpawnContext, opts: Options, item: str, readers: int) -> Throughput:
        label = f"SharedStream {mode} ({item}, {readers} readers)"
        with fresh_stream(opts, item) as name:
            ready, start, results = (
                ctx.Barrier(readers + 1),
                ctx.Barrier(readers + 2),
                ctx.Queue(),
            )
            reports = drive(
                label,
                opts,
                ctx,
                [(stream_reader, (name, mode, item))] * readers,
                (stream_sender, (name, item, opts.items[item])),
                ready,
                start,
                results,
            )
        return summarise(f"SharedStream {mode}", item, readers, reports)

    return run


def queue_run(ctx: SpawnContext, opts: Options, item: str, readers: int) -> Throughput:
    """One `mp.Queue` per reader; the sender puts every item on each."""
    label = f"mp.Queue ({item}, {readers} readers)"
    queues = [ctx.Queue(maxsize=opts.capacity) for _ in range(readers)]
    ready, start, results = (
        ctx.Barrier(readers + 1),
        ctx.Barrier(readers + 2),
        ctx.Queue(),
    )
    reports = drive(
        label,
        opts,
        ctx,
        [(queue_reader, (q,)) for q in queues],
        (queue_sender, (queues, item, opts.items[item])),
        ready,
        start,
        results,
    )
    return summarise("mp.Queue", item, readers, reports)


def ring_run(ctx: SpawnContext, opts: Options, item: str, readers: int) -> Throughput:
    """A lossless ring built from `SharedMemory` and one `Condition`, as a user would write it with the standard library."""
    label = f"SharedMemory ring + Lock ({item}, {readers} readers)"
    _, size = ring_layout(item, opts.capacity, readers)
    shm = SharedMemory(
        f"bench-ring-{os.getpid()}-{next(NAMES)}", create=True, size=size
    )
    try:
        cond = ctx.Condition()
        ready, start, results = (
            ctx.Barrier(readers + 1),
            ctx.Barrier(readers + 2),
            ctx.Queue(),
        )
        common = (shm.name, item, opts.capacity, readers)
        reports = drive(
            label,
            opts,
            ctx,
            [(ring_reader, (*common, i, cond, opts.timeout)) for i in range(readers)],
            (ring_sender, (*common, opts.items[item], cond, opts.timeout)),
            ready,
            start,
            results,
        )
    finally:
        shm.close()
        shm.unlink()
    return summarise("SharedMemory ring + Lock", item, readers, reports)


CONTENDERS = [
    stream_run("lossless"),
    stream_run("lossy"),
    stream_run("latest"),
    queue_run,
    ring_run,
]


def run_throughput(opts: Options) -> list[Throughput]:
    """Items per second of every contender, for every item size and reader count."""
    ctx = mp.get_context("spawn")
    rows = []
    for item in SIZES:
        for readers in opts.readers:
            for contender in CONTENDERS:
                runs = [
                    contender(ctx, opts, item, readers) for _ in range(opts.repeats)
                ]
                runs.sort(key=lambda row: row["received_per_s"])
                rows.append(runs[len(runs) // 2])
    return rows


def rate(value: float) -> str:
    """`value` per second with an SI suffix."""
    if value >= 1e6:
        return f"{value / 1e6:.1f}M/s"
    if value >= 1e3:
        return f"{value / 1e3:.0f}k/s"
    return f"{value:.0f}/s"


def to_text(rows: list[Throughput]) -> str:
    lines = [
        f"{'contender':26} {'item':8} {'readers':>7} {'received/s':>11} {'sent/s':>9} {'missed':>8}"
    ]
    lines += [
        f"{r['contender']:26} {r['item']:8} {r['readers']:7} "
        f"{rate(r['received_per_s']):>11} {rate(r['sent_per_s']):>9} {r['missed']:8}"
        for r in rows
    ]
    return "\n".join(lines)


def to_markdown(rows: list[Throughput]) -> str:
    lines = [
        "| contender | item | readers | received/s | sent/s | missed |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {r['contender']} | {r['item']} | {r['readers']} "
        f"| {rate(r['received_per_s'])} | {rate(r['sent_per_s'])} | {r['missed']} |"
        for r in rows
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--short", action="store_true")
    parser.add_argument("--json", type=Path, metavar="FILE")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument(
        "--table", choices=("throughput", "matrix", "both"), default="both"
    )
    args = parser.parse_args(argv)
    opts = Options.short() if args.short else Options()
    throughput = run_throughput(opts) if args.table in ("throughput", "both") else []
    matrix: list[Any] = []
    if args.json is not None:
        args.json.write_text(
            json.dumps({"throughput": throughput, "matrix": matrix}, indent=2)
        )
    show = to_markdown if args.markdown else to_text
    if throughput:
        print(show(throughput))


if __name__ == "__main__":
    main()
