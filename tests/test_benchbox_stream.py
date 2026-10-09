import itertools
import multiprocessing as mp
import os
import time
from multiprocessing.shared_memory import SharedMemory
from typing import Any

import pytest

from sharedbox import SegmentNotFoundError, SharedStream
from sharedbox.benchmarks import stream


def reader_that_fails(ready: Any, start: Any, results: Any) -> None:
    """Report, then exit with an error."""
    ready.wait()
    start.wait()
    results.put(("reader", 1, 1.0, 0))
    raise RuntimeError("boom")


def sender_that_finishes(start: Any, results: Any) -> None:
    """Report at once."""
    start.wait()
    results.put(("sender", 1, 1.0, 0))


def sender_that_hangs(start: Any, results: Any) -> None:
    """Wait on the start barrier, then sleep far past any timeout."""
    start.wait()
    time.sleep(60)


def drive_pair(timeout: float, sender: Any) -> list[Any]:
    """Run `drive` with `reader_that_fails` and `sender`."""
    ctx = mp.get_context("spawn")
    opts = stream.Options(timeout=timeout)
    return stream.drive(
        "pair",
        opts,
        ctx,
        [(reader_that_fails, ())],
        (sender, ()),
        ctx.Barrier(2),
        ctx.Barrier(3),
        ctx.Queue(),
    )


def test_throughput_reports_every_contender_and_mode() -> None:
    """Report a positive rate for every contender, item size and reader count, and no misses for lossless contenders."""
    rows = stream.run_throughput(stream.Options.short())
    labels = {r["contender"] for r in rows}
    assert labels == {
        "SharedStream lossless",
        "SharedStream lossy",
        "SharedStream latest",
        "mp.Queue",
        "SharedMemory ring + Lock",
    }
    assert {(r["item"], r["readers"]) for r in rows} == {
        (item, n) for item in stream.SIZES for n in (1, 4)
    }
    for r in rows:
        assert r["sent_per_s"] > 0 and r["received_per_s"] > 0
        if r["contender"] in (
            "SharedStream lossless",
            "mp.Queue",
            "SharedMemory ring + Lock",
        ):
            assert r["missed"] == 0


def test_a_stuck_contender_stops_with_its_name_and_unlinks_its_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End a run whose readers cannot finish in time with SystemExit naming the contender and leave no stream."""
    monkeypatch.setattr(stream, "NAMES", itertools.count(9000))
    opts = stream.Options({"1 KiB": 200, "512 KiB": 20}, (1,), 4, 10, 1, 0.000001)
    with pytest.raises(SystemExit, match="SharedStream lossless"):
        stream.run_throughput(opts)
    with pytest.raises(SegmentNotFoundError):
        SharedStream.attach(stream.Small, f"bench-stream-{os.getpid()}-9000")


def test_a_stuck_ring_run_removes_its_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Release the shared memory of a ring run that cannot finish in time."""
    monkeypatch.setattr(stream, "NAMES", itertools.count(9000))
    opts = stream.Options({"1 KiB": 200, "512 KiB": 20}, (1,), 4, 10, 1, 0.000001)
    with pytest.raises(SystemExit, match="SharedMemory ring"):
        stream.ring_run(mp.get_context("spawn"), opts, "1 KiB", 1)
    with pytest.raises(FileNotFoundError):
        SharedMemory(f"bench-ring-{os.getpid()}-9000")


def test_a_failed_child_stops_the_run_with_the_contender_name() -> None:
    """Raise SystemExit naming the contender when a child exits with an error after reporting."""
    with pytest.raises(SystemExit, match="pair: a process failed"):
        drive_pair(30.0, sender_that_finishes)


def test_a_child_that_outlives_the_timeout_is_terminated() -> None:
    """Raise SystemExit naming the contender and end the child at once, not after its sleep."""
    begin = time.monotonic()
    with pytest.raises(SystemExit, match="pair: not finished"):
        drive_pair(5.0, sender_that_hangs)
    assert time.monotonic() - begin < 30


def test_matrix_reports_every_sender_and_reader() -> None:
    """Report a rate and ordered latency percentiles for each sender and reader pairing and item size."""
    rows = stream.run_matrix(stream.Options.short())
    pairs = {(r["sender"], r["reader"]) for r in rows}
    assert pairs == {
        (s, r)
        for s in ("send", "asend")
        for r in ("receive", "async for", "events.received")
    } | {
        ("send", "async for (buffered)"),
        ("mp.Queue put", "mp.Queue get"),
        ("mp.Queue put", "asyncio + mp.Queue"),
    }
    assert {r["item"] for r in rows} == set(stream.SIZES)
    for r in rows:
        assert r["items_per_s"] > 0
        if r["reader"] == "async for (buffered)":
            assert r["p50_us"] is r["p90_us"] is r["p99_us"] is None
        else:
            p50, p90, p99 = r["p50_us"], r["p90_us"], r["p99_us"]
            assert p50 is not None and p90 is not None and p99 is not None
            assert 0 < p50 <= p90 <= p99


def test_matrix_tables_show_a_dash_for_a_row_without_latency() -> None:
    """Print `-` in the percentile columns of a row whose latency is None."""
    row = stream.Matrix(
        sender="send",
        reader="async for (buffered)",
        item="1 KiB",
        items_per_s=1e6,
        p50_us=None,
        p90_us=None,
        p99_us=None,
    )
    assert stream.matrix_to_text([row]).splitlines()[1].split()[-3:] == ["-"] * 3
    assert stream.matrix_to_markdown([row]).splitlines()[2].endswith("| - | - | - |")
