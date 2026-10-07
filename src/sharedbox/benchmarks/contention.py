"""Throughput and latency with several writer and reader processes at once.

Every process runs the same number of operations, starting together. A time
per operation includes the cost of reading the clock once.

    python -m sharedbox.benchmarks.contention
    python -m sharedbox.benchmarks.contention --writers 1,4 --readers 0 --ops 50000
"""

import argparse
import multiprocessing as mp
import os
import queue
import statistics
import struct
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from itertools import pairwise
from multiprocessing.context import SpawnContext
from multiprocessing.queues import Queue
from multiprocessing.shared_memory import SharedMemory
from multiprocessing.synchronize import Barrier
from typing import Any, Literal, NamedTuple, TypedDict

from sharedbox import SharedBox

WRITERS = (1, 2, 4)
READERS = (0, 2)
OPS = 100_000
TIMEOUT = 120.0
INT = struct.Struct("<q")

Role = Literal["write", "read"]


@dataclass(frozen=True)
class Options:
    """The process counts to combine, and how many operations each process runs."""

    writers: tuple[int, ...] = WRITERS
    readers: tuple[int, ...] = READERS
    ops: int = OPS
    timeout: float = TIMEOUT


class Result(TypedDict):
    contender: str
    writers: int
    readers: int
    write_mops: float
    write_p50_ns: float
    write_p99_ns: float
    read_mops: float | None
    read_p50_ns: float | None
    read_p99_ns: float | None


class Counters(SharedBox):
    f0: int = 0
    f1: int = 0
    f2: int = 0
    f3: int = 0
    f4: int = 0
    f5: int = 0
    f6: int = 0
    f7: int = 0


class Ends(NamedTuple):
    write: Callable[[int], object]
    read: Callable[[], object]
    close: Callable[[], object]


class Contender(NamedTuple):
    label: str
    create: Callable[[SpawnContext, str], tuple[Any, Callable[[], object]]]
    open: Callable[[Any, int], Ends]


def create_box(_: SpawnContext, name: str) -> tuple[str, Callable[[], object]]:
    box = Counters.create(name)

    def cleanup() -> None:
        box.close()
        Counters.unlink(name)

    return name, cleanup


def open_box(name: str, index: int, own_field: bool) -> Ends:
    box = Counters.attach(name)
    field = f"f{index % 8}" if own_field else "f0"
    return Ends(partial(setattr, box, field), partial(getattr, box, "f0"), box.close)


def create_value(ctx: SpawnContext, _: str) -> tuple[Any, Callable[[], object]]:
    return ctx.Value("q", 0), lambda: None


def open_value(value: Any, _: int) -> Ends:
    return Ends(
        partial(setattr, value, "value"),
        partial(getattr, value, "value"),
        lambda: None,
    )


def create_memory(ctx: SpawnContext, name: str) -> tuple[Any, Callable[[], object]]:
    shm = SharedMemory(name, create=True, size=8)

    def cleanup() -> None:
        shm.close()
        shm.unlink()

    return (name, ctx.Lock()), cleanup


def open_memory(handle: tuple[str, Any], _: int) -> Ends:
    name, lock = handle
    shm = SharedMemory(name)
    if shm.buf is None:
        raise RuntimeError(f"shared memory {name} is closed")
    buf = shm.buf

    def write(value: int) -> None:
        with lock:
            INT.pack_into(buf, 0, value)

    def read() -> object:
        with lock:
            return INT.unpack_from(buf, 0)[0]

    return Ends(write, read, shm.close)


CONTENDERS = {
    "box": Contender(
        "SharedBox, one field", create_box, partial(open_box, own_field=False)
    ),
    "box-own": Contender(
        "SharedBox, field per writer", create_box, partial(open_box, own_field=True)
    ),
    "value": Contender("mp.Value", create_value, open_value),
    "memory": Contender("SharedMemory + Lock", create_memory, open_memory),
}


def worker(
    key: str,
    handle: Any,
    role: Role,
    index: int,
    ops: int,
    barrier: Barrier,
    results: "Queue[tuple[Role, bytes]]",
    timeout: float,
) -> None:
    ends = CONTENDERS[key].open(handle, index)
    stamps = array("q", bytes(8 * (ops + 1)))
    # perf_counter, not monotonic: on Windows before CPython 3.13 monotonic
    # ticks every 15.6 ms. Both platforms share its clock between processes.
    now = time.perf_counter_ns
    barrier.wait(timeout)
    if role == "write":
        write = ends.write
        for i in range(ops):
            stamps[i] = now()
            write(i)
    else:
        read = ends.read
        for i in range(ops):
            stamps[i] = now()
            read()
    stamps[ops] = now()
    ends.close()
    results.put((role, stamps.tobytes()))


def summarise(runs: "list[array[int]]") -> tuple[float, float, float]:
    """Million operations per second over all runs, then p50 and p99 in ns."""
    span = max(run[-1] for run in runs) - min(run[0] for run in runs)
    ops = sum(len(run) - 1 for run in runs)
    times = [b - a for run in runs for a, b in pairwise(run)]
    cuts = statistics.quantiles(times, n=100)
    return ops / span * 1000, cuts[49], cuts[98]


def run_one(
    ctx: SpawnContext, key: str, writers: int, readers: int, opts: Options
) -> Result:
    contender = CONTENDERS[key]
    handle, cleanup = contender.create(ctx, f"bench-contention-{os.getpid()}-{key}")
    roles: list[Role] = [
        "write" if i < writers else "read" for i in range(writers + readers)
    ]
    barrier = ctx.Barrier(len(roles))
    results: Queue[tuple[Role, bytes]] = ctx.Queue()
    children = [
        ctx.Process(
            target=worker,
            args=(key, handle, role, i, opts.ops, barrier, results, opts.timeout),
            daemon=True,
        )
        for i, role in enumerate(roles)
    ]
    runs: dict[Role, list[array[int]]] = {"write": [], "read": []}
    try:
        for child in children:
            child.start()
        for _ in children:
            try:
                role, data = results.get(timeout=opts.timeout)
            except queue.Empty:
                raise SystemExit(
                    f"{contender.label}: a process gave no result within "
                    f"{opts.timeout} s; a busy machine may need a longer --timeout"
                ) from None
            stamps = array("q")
            stamps.frombytes(data)
            runs[role].append(stamps)
    finally:
        for child in children:
            child.join(opts.timeout)
            if child.is_alive():
                child.terminate()
        cleanup()
    write = summarise(runs["write"])
    read = summarise(runs["read"]) if readers else (None, None, None)
    return Result(
        contender=contender.label,
        writers=writers,
        readers=readers,
        write_mops=write[0],
        write_p50_ns=write[1],
        write_p99_ns=write[2],
        read_mops=read[0],
        read_p50_ns=read[1],
        read_p99_ns=read[2],
    )


def run(opts: Options) -> list[Result]:
    """Every contender with every combination of writer and reader counts."""
    ctx = mp.get_context("spawn")
    return [
        run_one(ctx, key, writers, readers, opts)
        for writers in opts.writers
        for readers in opts.readers
        for key in CONTENDERS
    ]


def cells(r: Result) -> list[str]:
    def number(value: float | None, digits: int) -> str:
        return "-" if value is None else f"{value:.{digits}f}"

    return [
        r["contender"],
        str(r["writers"]),
        str(r["readers"]),
        number(r["write_mops"], 2),
        number(r["write_p50_ns"], 0),
        number(r["write_p99_ns"], 0),
        number(r["read_mops"], 2),
        number(r["read_p50_ns"], 0),
        number(r["read_p99_ns"], 0),
    ]


HEADERS = [
    "contender",
    "writers",
    "readers",
    "write M/s",
    "write p50 ns",
    "write p99 ns",
    "read M/s",
    "read p50 ns",
    "read p99 ns",
]


def to_text(results: list[Result]) -> str:
    rows = [HEADERS, *(cells(r) for r in results)]
    widths = [max(len(row[i]) for row in rows) for i in range(len(HEADERS))]
    return "\n".join(
        " ".join(
            cell.ljust(width) if i == 0 else cell.rjust(width)
            for i, (cell, width) in enumerate(zip(row, widths, strict=True))
        )
        for row in rows
    )


def to_markdown(results: list[Result]) -> str:
    lines = [
        "| " + " | ".join(HEADERS) + " |",
        "|" + " --- |" * len(HEADERS),
    ]
    lines += ["| " + " | ".join(cells(r)) + " |" for r in results]
    return "\n".join(lines)


def counts(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(","))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--writers", type=counts, default=WRITERS)
    parser.add_argument("--readers", type=counts, default=READERS)
    parser.add_argument("--ops", type=int, default=OPS)
    parser.add_argument("--timeout", type=float, default=TIMEOUT)
    args = parser.parse_args(argv)
    opts = Options(args.writers, args.readers, args.ops, args.timeout)
    print(to_text(run(opts)))


if __name__ == "__main__":
    main()
