import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sharedbox.benchmarks import _app, roundtrip, size, stream

THROUGHPUT: list[stream.Throughput] = [
    {
        "contender": "SharedStream",
        "item": "1 KiB",
        "readers": 1,
        "sent_per_s": 2000.0,
        "received_per_s": 1900.0,
        "received_per_s_min": 1800.0,
        "received_per_s_max": 1950.0,
        "missed": 0,
    }
]
MATRIX: list[stream.Matrix] = [
    {
        "sender": "sync",
        "reader": "buffered",
        "item": "1 KiB",
        "items_per_s": 1500.0,
        "p50_us": None,
        "p90_us": None,
        "p99_us": None,
    }
]
ROUNDTRIP: list[roundtrip.Result] = [
    {
        "contender": "SharedStream",
        "p50_us": 10.0,
        "p90_us": 12.0,
        "p99_us": 20.0,
        "max_us": 30.0,
        "samples": 5,
    }
]


def test_all_writes_the_stream_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write stream.json with both tables and a Streams section in summary.md."""
    monkeypatch.setattr(_app, "run_ops", lambda *args: 0)
    monkeypatch.setattr(_app, "ops_markdown", lambda path: "ops table")
    monkeypatch.setattr(roundtrip, "run", lambda opts: ROUNDTRIP)
    monkeypatch.setattr(stream, "run_throughput", lambda opts: THROUGHPUT)
    monkeypatch.setattr(stream, "run_matrix", lambda opts: MATRIX)
    monkeypatch.setattr(size, "default_wheels", list)

    result = CliRunner().invoke(_app.app, ["all", "--out", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert json.loads((tmp_path / "stream.json").read_text()) == {
        "throughput": THROUGHPUT,
        "matrix": MATRIX,
    }
    assert "## Streams" in (tmp_path / "summary.md").read_text(encoding="utf-8")
