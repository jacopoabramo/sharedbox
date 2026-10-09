"""Stream throughput and the sync/async matrix, in spawned processes.

benchbox stream
python -m sharedbox.benchmarks.stream --short --json stream.json
"""

import argparse
import asyncio
import json
import multiprocessing as mp
import os
import queue
import statistics
import threading
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import count
from multiprocessing.context import SpawnContext, SpawnProcess
from multiprocessing.queues import Queue
from multiprocessing.shared_memory import SharedMemory
from multiprocessing.synchronize import Barrier, Condition
from pathlib import Path
from threading import BrokenBarrierError
from typing import Annotated, Any, Literal, TypedDict

import numpy as np

from sharedbox import DType, EndOfStream, Shape, SharedStream

NAMES = count()
Mode = Literal["lossless", "lossy", "latest"]
Report = tuple[str, int, float, Any]
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
    received_per_s_min: float
    received_per_s_max: float
    missed: int


class Matrix(TypedDict):
    sender: str
    reader: str
    item: str
    items_per_s: float
    p50_us: float | None
    p90_us: float | None
    p99_us: float | None


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
        received_per_s_min=min(received),
        received_per_s_max=max(received),
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


ROWS = [
    (sender, reader)
    for sender in ("send", "asend")
    for reader in ("receive", "async for", "events.received")
] + [
    ("send", "async for (buffered)"),
    ("mp.Queue put", "mp.Queue get"),
    ("mp.Queue put", "asyncio + mp.Queue"),
]
GAP = 0.001


@dataclass
class Tally:
    """Counts the items a reader takes and times the first `total` of them.

    Each later item is a latency sample: the time since its sender stamped it.
    """

    total: int
    latency: int
    begin: float = 0.0
    secs: float = 0.0
    got: int = 0
    samples: list[int] = field(default_factory=list)

    def take(self, stamp: int) -> None:
        self.got += 1
        if self.got == self.total:
            self.secs = time.perf_counter() - self.begin
        elif self.got > self.total:
            self.samples.append(time.perf_counter_ns() - stamp)

    def report(self) -> Report:
        """The reader's report, once every item arrived."""
        if self.got != self.total + self.latency:
            raise RuntimeError(f"took {self.got} of {self.total + self.latency} items")
        return ("reader", self.total, self.secs, self.samples)


def stamps(total: int, latency: int) -> Generator[int, None, None]:
    """A stamp per item: 0 for the first `total`, then the time, one item per `GAP`."""
    for i in range(total + latency):
        if i >= total:
            time.sleep(GAP)
        yield time.perf_counter_ns() if i >= total else 0


async def astamps(total: int, latency: int) -> AsyncGenerator[int, None]:
    """`stamps`, sleeping without blocking the event loop."""
    for i in range(total + latency):
        if i >= total:
            await asyncio.sleep(GAP)
        yield time.perf_counter_ns() if i >= total else 0


def read_receive(reader: Any, tally: Tally) -> None:
    try:
        while True:
            tally.take(reader.receive().stamp)
    except EndOfStream:
        pass


async def read_async(reader: Any, tally: Tally) -> None:
    async for item in reader:
        tally.take(item.stamp)


def read_events(reader: Any, tally: Tally) -> None:
    ended = threading.Event()
    reader.events.ended.connect(lambda: ended.set())
    reader.events.received.connect(lambda item, position: tally.take(item.stamp))
    ended.wait()


async def read_buffered(
    reader: Any, tally: Tally, rounds: int, capacity: int, gate: Barrier
) -> None:
    """Time every `anext` of a full ring, `rounds` times.

    The sender fills the ring and meets the reader at `gate`; the reader drains
    the ring and meets the sender again before the next round.
    """
    for _ in range(rounds):
        gate.wait()
        for _ in range(capacity):
            before = time.perf_counter_ns()
            await anext(reader)
            tally.secs += (time.perf_counter_ns() - before) / 1e9
            tally.got += 1
        gate.wait()


def matrix_stream_reader(
    name: str,
    how: str,
    item: str,
    total: int,
    latency: int,
    capacity: int,
    gate: Barrier,
    ready: Barrier,
    start: Barrier,
    results: Any,
) -> None:
    """Receive `total + latency` items the way `how` says and report the timings."""
    stream = SharedStream.attach(SIZES[item], name)
    reader = stream.reader(mode="lossless", start="oldest")
    tally = Tally(total, latency)
    ready.wait()
    start.wait()
    tally.begin = time.perf_counter()
    match how:
        case "receive":
            read_receive(reader, tally)
        case "async for":
            asyncio.run(read_async(reader, tally))
        case "events.received":
            read_events(reader, tally)
        case "async for (buffered)":
            rounds = total // capacity
            asyncio.run(read_buffered(reader, tally, rounds, capacity, gate))
        case _:
            raise ValueError(f"unknown reader {how!r}")
    results.put(tally.report())
    stream.close()


async def asend_all(sender: Any, item: str, total: int, latency: int) -> None:
    data = zeros(item)
    async for stamp in astamps(total, latency):
        await sender.asend(SIZES[item](stamp, data))


def matrix_stream_sender(
    name: str,
    how: str,
    item: str,
    total: int,
    latency: int,
    fill: int,
    gate: Barrier,
    start: Barrier,
    results: Any,
) -> None:
    """Send `total` items at full speed, then `latency` stamped ones, `GAP` apart.

    With `fill`, send `total` items in rounds of `fill`, each meeting the reader
    at `gate` when the ring is full and again when it is empty.
    """
    stream = SharedStream.attach(SIZES[item], name)
    data = zeros(item)
    start.wait()
    begin = time.perf_counter()
    with stream.sender() as sender:
        if how == "asend":
            asyncio.run(asend_all(sender, item, total, latency))
        elif fill:
            for _ in range(total // fill):
                for _ in range(fill):
                    sender.send(SIZES[item](0, data), timeout=None)
                gate.wait()
                gate.wait()
        else:
            for stamp in stamps(total, latency):
                sender.send(SIZES[item](stamp, data), timeout=None)
    results.put(("sender", total, time.perf_counter() - begin, []))
    stream.close()


async def get_in_executor(source: Any, tally: Tally) -> None:
    loop = asyncio.get_running_loop()
    while (message := await loop.run_in_executor(None, source.get)) is not None:
        tally.take(message[0])


def matrix_queue_reader(
    source: Any,
    how: str,
    total: int,
    latency: int,
    ready: Barrier,
    start: Barrier,
    results: Any,
) -> None:
    """Take from `source` until `None`, blocking or in an executor, and report the timings."""
    tally = Tally(total, latency)
    ready.wait()
    start.wait()
    tally.begin = time.perf_counter()
    if how == "mp.Queue get":
        while (message := source.get()) is not None:
            tally.take(message[0])
    else:
        asyncio.run(get_in_executor(source, tally))
    results.put(tally.report())


def matrix_queue_sender(
    sink: Any, item: str, total: int, latency: int, start: Barrier, results: Any
) -> None:
    """Put `total` items at full speed, then `latency` stamped ones, `GAP` apart, then `None`."""
    data = zeros(item)
    payload = data.tobytes() if item == "1 KiB" else data
    start.wait()
    begin = time.perf_counter()
    for stamp in stamps(total, latency):
        sink.put((stamp, payload))
    sink.put(None)
    results.put(("sender", total, time.perf_counter() - begin, []))


def matrix_run(
    ctx: SpawnContext, opts: Options, sender: str, reader: str, item: str
) -> Matrix:
    """One sender and reader pairing: the rate over `opts.items[item]` items, then latency percentiles."""
    label = f"{sender} -> {reader} ({item})"
    buffered = reader.endswith("(buffered)")
    total = opts.items[item]
    if buffered:
        total = total // opts.capacity * opts.capacity
    latency = 0 if buffered else opts.latency_items
    ready, start, results = ctx.Barrier(2), ctx.Barrier(3), ctx.Queue()
    if sender == "mp.Queue put":
        sink = ctx.Queue(maxsize=opts.capacity)
        reports = drive(
            label,
            opts,
            ctx,
            [(matrix_queue_reader, (sink, reader, total, latency))],
            (matrix_queue_sender, (sink, item, total, latency)),
            ready,
            start,
            results,
        )
    else:
        gate = ctx.Barrier(2)
        fill = opts.capacity if buffered else 0
        with fresh_stream(opts, item) as name:
            reports = drive(
                label,
                opts,
                ctx,
                [
                    (
                        matrix_stream_reader,
                        (name, reader, item, total, latency, opts.capacity, gate),
                    )
                ],
                (
                    matrix_stream_sender,
                    (name, sender, item, total, latency, fill, gate),
                ),
                ready,
                start,
                results,
            )
    _, got, secs, samples = next(r for r in reports if r[0] == "reader")
    cuts = None if buffered else statistics.quantiles(samples, n=100)
    return Matrix(
        sender=sender,
        reader=reader,
        item=item,
        items_per_s=got / secs,
        p50_us=None if cuts is None else cuts[49] / 1000,
        p90_us=None if cuts is None else cuts[89] / 1000,
        p99_us=None if cuts is None else cuts[98] / 1000,
    )


def run_matrix(opts: Options) -> list[Matrix]:
    """Rate and latency of every sender and reader pairing, for every item size."""
    ctx = mp.get_context("spawn")
    rows = []
    for item in SIZES:
        for sender, reader in ROWS:
            runs = [
                matrix_run(ctx, opts, sender, reader, item) for _ in range(opts.repeats)
            ]
            runs.sort(key=lambda row: row["items_per_s"])
            rows.append(runs[len(runs) // 2])
    return rows


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
                rows.append(
                    runs[len(runs) // 2]
                    | {
                        "received_per_s_min": runs[0]["received_per_s"],
                        "received_per_s_max": runs[-1]["received_per_s"],
                    }
                )
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


def micros(value: float | None) -> str:
    """`value` in microseconds, or `-` when the row has no latency."""
    return "-" if value is None else f"{value:.1f}"


def matrix_to_text(rows: list[Matrix]) -> str:
    lines = [
        f"{'sender':13} {'reader':22} {'item':8} {'items/s':>9} {'p50 us':>9} {'p90 us':>9} {'p99 us':>9}"
    ]
    lines += [
        f"{r['sender']:13} {r['reader']:22} {r['item']:8} {rate(r['items_per_s']):>9} "
        f"{micros(r['p50_us']):>9} {micros(r['p90_us']):>9} {micros(r['p99_us']):>9}"
        for r in rows
    ]
    return "\n".join(lines)


def matrix_to_markdown(rows: list[Matrix]) -> str:
    lines = [
        "| sender | reader | item | items/s | p50 us | p90 us | p99 us |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {r['sender']} | {r['reader']} | {r['item']} | {rate(r['items_per_s'])} "
        f"| {micros(r['p50_us'])} | {micros(r['p90_us'])} | {micros(r['p99_us'])} |"
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
    matrix = run_matrix(opts) if args.table in ("matrix", "both") else []
    if args.json is not None:
        args.json.write_text(
            json.dumps({"throughput": throughput, "matrix": matrix}, indent=2)
        )
    if throughput:
        print((to_markdown if args.markdown else to_text)(throughput))
    if matrix:
        print((matrix_to_markdown if args.markdown else matrix_to_text)(matrix))


if __name__ == "__main__":
    main()
