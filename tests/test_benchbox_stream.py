import pytest

from sharedbox.benchmarks import stream


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


def test_a_stuck_contender_stops_with_its_name() -> None:
    """End a run whose readers cannot finish in time with SystemExit naming the contender."""
    opts = stream.Options({"1 KiB": 200, "512 KiB": 20}, (1,), 4, 10, 1, 0.000001)
    with pytest.raises(SystemExit, match="SharedStream lossless"):
        stream.run_throughput(opts)
