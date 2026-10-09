import json
from pathlib import Path
from typing import Any

import pyperf
import pytest

from sharedbox.benchmarks import plot

OPS = {
    "read int/SharedBox": 50e-9,
    "read int/mp.Value/Array": 120e-9,
    "read int/Manager().Namespace()": 9e-6,
    "read ref/SharedBox": 300e-9,
}
ROUNDTRIP = ["SharedBox.watch()", "mp.Event", "mp.Pipe"]


def write_results(folder: Path) -> None:
    benchmarks = [
        pyperf.Benchmark(
            [pyperf.Run([s, s * 1.1], metadata={"name": name}, collect_metadata=False)]
        )
        for name, s in OPS.items()
    ]
    pyperf.BenchmarkSuite(benchmarks).dump(str(folder / "ops.json"))
    results = [
        {
            "contender": name,
            "p50_us": 10.0 * i,
            "p90_us": 20.0 * i,
            "p99_us": 40.0 * i,
            "max_us": 80.0 * i,
            "samples": 100,
        }
        for i, name in enumerate(ROUNDTRIP, start=1)
    ]
    (folder / "roundtrip.json").write_text(json.dumps(results), encoding="utf-8")
    throughput = [
        {
            "contender": f"{name} (1 KiB, 1 readers)",
            "item": "1 KiB",
            "readers": 1,
            "sent_per_s": rate * 1.5,
            "received_per_s": rate,
            "missed": 0,
        }
        for name, rate in (("SharedStream lossless", 5e5), ("mp.Queue", 5e4))
    ]
    matrix = [
        {
            "sender": sender,
            "reader": reader,
            "item": "1 KiB",
            "items_per_s": 1e5,
            "p50_us": p50,
            "p90_us": None if p50 is None else p50 * 2,
            "p99_us": None if p50 is None else p50 * 4,
        }
        for sender, reader, p50 in (
            ("send", "receive", 12.0),
            ("send", "async for (buffered)", None),
            ("mp.Queue put", "mp.Queue get", 80.0),
        )
    ]
    (folder / "stream.json").write_text(
        json.dumps({"throughput": throughput, "matrix": matrix}), encoding="utf-8"
    )
    (folder / "summary.md").write_text(
        "# sharedbox benchmarks\n\n- OS: TestOS 1\n- CPU: TestCPU, 8 logical cores\n",
        encoding="utf-8",
    )


def read_chart(folder: Path, name: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(
        (folder / "charts" / name).read_text(encoding="utf-8")
    )
    return result


def test_plot_writes_every_chart_naming_every_contender(tmp_path: Path) -> None:
    """Write one plotly figure per chart, naming every contender, marking sharedbox rows as accent and leaving out single-contender operations."""
    write_results(tmp_path)
    written = plot.write_charts(tmp_path)
    assert sorted(p.name for p in written) == [
        "ops.json",
        "roundtrip.json",
        "stream-matrix.json",
        "stream-throughput.json",
    ]
    ops = read_chart(tmp_path, "ops.json")
    names = {y for trace in ops["data"] for y in trace["y"]}
    assert {"SharedBox", "mp.Value/Array", "Manager().Namespace()"} <= names
    assert all(t["meta"] in ("accent", "other") for t in ops["data"])
    assert "read ref" not in json.dumps(ops)
    assert "TestCPU" in json.dumps(ops)
    roundtrip = read_chart(tmp_path, "roundtrip.json")
    assert {y for trace in roundtrip["data"] for y in trace["y"]} == set(ROUNDTRIP)


def test_plot_leaves_the_buffered_row_off_the_latency_axis(tmp_path: Path) -> None:
    """Draw the matrix without the pairing that has no latency, and mark stream senders as accent."""
    write_results(tmp_path)
    plot.write_charts(tmp_path)
    matrix = read_chart(tmp_path, "stream-matrix.json")
    kinds = {trace["y"][0]: trace["meta"] for trace in matrix["data"] if "y" in trace}
    assert kinds == {
        "send / receive": "accent",
        "mp.Queue put / mp.Queue get": "other",
    }


def test_plot_writes_the_same_bytes_twice(tmp_path: Path) -> None:
    """Write identical files on a second run, so committed charts only change with the results."""
    write_results(tmp_path)
    first = [p.read_bytes() for p in plot.write_charts(tmp_path)]
    second = [p.read_bytes() for p in plot.write_charts(tmp_path)]
    assert first == second


def test_plot_skips_charts_whose_results_are_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Draw the charts whose results exist and name the ones that are missing."""
    write_results(tmp_path)
    (tmp_path / "stream.json").unlink()
    written = plot.write_charts(tmp_path)
    assert sorted(p.name for p in written) == ["ops.json", "roundtrip.json"]
    assert "skipped stream-matrix.json: no stream.json" in capsys.readouterr().err
