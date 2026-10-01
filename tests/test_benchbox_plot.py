import json
from pathlib import Path

import pyperf

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


def test_plot_draws_both_charts_for_both_themes_naming_every_contender(
    tmp_path: Path,
) -> None:
    """Check that the charts come in a light and a dark variant and name each contender, and that operations timed for one contender are left out."""
    write_results(tmp_path)
    written = plot.write_charts(tmp_path)
    assert sorted(path.name for path in written) == [
        "ops-dark.svg",
        "ops-light.svg",
        "roundtrip-dark.svg",
        "roundtrip-light.svg",
    ]
    for theme in ("light", "dark"):
        ops = (tmp_path / f"ops-{theme}.svg").read_text(encoding="utf-8")
        for name in (
            "SharedBox",
            "mp.Value/Array",
            "Manager().Namespace()",
            "read int",
        ):
            assert name in ops
        assert "read ref" not in ops
        roundtrip = (tmp_path / f"roundtrip-{theme}.svg").read_text(encoding="utf-8")
        for name in ROUNDTRIP:
            assert name in roundtrip
