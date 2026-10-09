"""Charts of the results of `benchbox all`, as plotly figure JSON for the docs site."""

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from math import log10
from pathlib import Path
from typing import Any, NamedTuple

import plotly.graph_objects as go
import pyperf
from plotly.subplots import make_subplots

from sharedbox.benchmarks.roundtrip import Result
from sharedbox.benchmarks.stream import Matrix, Throughput

ROW_HEIGHT = 30
PANEL_GAP = 60
CHAR_WIDTH = 7.5
MARGIN_TOP = 45
NOTE_LINE = 15
MARGIN_BOTTOM = 60
SHARED_PREFIXES = ("SharedBox", "SharedStream", "send", "asend")


class Row(NamedTuple):
    """One contender in one panel: a dot at `mid` and a line from `low` to `high`."""

    name: str
    low: float
    mid: float
    high: float
    hover: str
    label: str


def scale_text(value: float, units: tuple[tuple[float, str], ...]) -> str:
    """`value` with the largest unit it reaches, such as `1 us`."""
    for size, unit in units:
        if value >= size:
            return f"{value / size:.3g} {unit}"
    size, unit = units[-1]
    return f"{value / size:.3g} {unit}"


TIME = ((1e9, "s"), (1e6, "ms"), (1e3, "us"), (1.0, "ns"))
RATE = ((1e6, "M/s"), (1e3, "k/s"), (1.0, "/s"))


def decades(low: float, high: float) -> list[float]:
    """Powers of ten from the one at or below `low` to the one at or above `high`."""
    steps = []
    step = 1.0
    while step < low / 10:
        step *= 10
    while step < high * 10:
        steps.append(step)
        step *= 10
    return steps


def axis(
    rows: list[Row], units: tuple[tuple[float, str], ...], unit_scale: float
) -> dict[str, Any]:
    """Log x axis settings with a tick at each power of ten, written with its unit.

    `unit_scale` converts the values of the rows to the base unit of `units`.
    """
    low = min(row.low for row in rows) * unit_scale
    high = max(row.high for row in rows) * unit_scale
    steps = decades(low, high)
    return {
        "type": "log",
        "range": [log10(low / unit_scale) - 0.2, log10(high / unit_scale) + 0.2],
        "tickvals": [step / unit_scale for step in steps],
        "ticktext": [scale_text(step, units) for step in steps],
        "showgrid": True,
        "zeroline": False,
    }


def machine_note(folder: Path) -> str:
    """The machine lines of `summary.md`, the pyperf version and the date of the results."""
    parts = []
    summary = folder / "summary.md"
    if summary.exists():
        wanted = ("- OS:", "- CPU:", "- Python:", "- sharedbox:")
        parts = [
            line[2:]
            for line in summary.read_text(encoding="utf-8").splitlines()
            if line.startswith(wanted)
        ]
    stamp = max(path.stat().st_mtime for path in folder.glob("*.json"))
    return "<br>".join(
        [
            *parts,
            f"pyperf {pyperf.__version__}; "
            + datetime.fromtimestamp(stamp, tz=UTC).date().isoformat(),
        ]
    )


def panels_figure(
    panels: dict[str, list[Row]],
    units: tuple[tuple[float, str], ...],
    unit_scale: float,
    x_title: str,
    note: str,
) -> go.Figure:
    """Small multiples with a shared log x axis: one panel per key of `panels`.

    Every trace carries `meta` of `accent` or `other`; the page script
    chooses the colours, so the figure holds none.
    """
    rows_total = sum(len(rows) for rows in panels.values())
    plot_height = rows_total * ROW_HEIGHT + (len(panels) - 1) * PANEL_GAP
    fig = make_subplots(
        rows=len(panels),
        cols=1,
        shared_xaxes=True,
        subplot_titles=list(panels),
        row_heights=[len(rows) for rows in panels.values()],
        vertical_spacing=PANEL_GAP / plot_height if len(panels) > 1 else 0,
    )
    fig.for_each_annotation(lambda a: a.update(x=0, xanchor="left", yshift=22))
    for index, rows in enumerate(panels.values(), start=1):
        for row in rows:
            kind = "accent" if row.name.startswith(SHARED_PREFIXES) else "other"
            fig.add_trace(
                go.Scatter(
                    x=[row.low, row.high],
                    y=[row.name, row.name],
                    mode="lines",
                    line={"width": 2},
                    hoverinfo="skip",
                    meta=kind,
                    uid=f"{index}-{row.name}-range",
                ),
                row=index,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=[row.mid],
                    y=[row.name],
                    mode="markers+text",
                    marker={"size": 10},
                    text=[row.label],
                    textposition="top center",
                    cliponaxis=False,
                    hovertemplate=row.hover + "<extra></extra>",
                    meta=kind,
                    uid=f"{index}-{row.name}-median",
                ),
                row=index,
                col=1,
            )
    bottom = MARGIN_BOTTOM + NOTE_LINE * (note.count("<br>") + 1)
    left = 20 + CHAR_WIDTH * max(
        len(row.name) for rows in panels.values() for row in rows
    )
    fig.update_layout(
        template=None,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        height=plot_height + MARGIN_TOP + bottom,
        margin={"l": left, "r": 60, "t": MARGIN_TOP, "b": bottom},
    )
    every = [row for rows in panels.values() for row in rows]
    fig.update_xaxes(**axis(every, units, unit_scale))
    fig.update_xaxes(title_text=x_title, row=len(panels), col=1)
    fig.update_yaxes(autorange="reversed", showgrid=False, zeroline=False)
    fig.add_annotation(
        text=note,
        xref="paper",
        yref="paper",
        x=0,
        y=-(MARGIN_BOTTOM - 5) / plot_height,
        xanchor="left",
        align="left",
        yanchor="top",
        showarrow=False,
        name="note",
    )
    return fig


def ops_panels(path: Path) -> dict[str, list[Row]]:
    """The median, p10 and p90 of each contender in ns, by operation.

    Operations timed for one contender only are left out, since they compare
    nothing.
    """
    panels: dict[str, list[Row]] = {}
    for bench in pyperf.BenchmarkSuite.load(str(path)).get_benchmarks():
        operation, _, name = bench.get_name().partition("/")
        median, p10, p90 = (
            bench.median() * 1e9,
            bench.percentile(10) * 1e9,
            bench.percentile(90) * 1e9,
        )
        panels.setdefault(operation, []).append(
            Row(
                name,
                p10,
                median,
                p90,
                f"{name}<br>median %{{x:.3g}} ns<br>p10 {p10:.3g} ns, p90 {p90:.3g} ns",
                scale_text(median, TIME),
            )
        )
    return {op: rows for op, rows in panels.items() if len(rows) > 1}


def ops_figure(path: Path, note: str) -> go.Figure:
    """One panel per operation: the median, with a line from p10 to p90."""
    return panels_figure(
        ops_panels(path), TIME, 1.0, "Time per operation (log scale)", note
    )


def roundtrip_figure(results: list[Result], note: str) -> go.Figure:
    """One row per contender: the p50, with a line from p50 to p99."""
    rows = [
        Row(
            r["contender"],
            r["p50_us"],
            r["p50_us"],
            r["p99_us"],
            f"{r['contender']}<br>p50 %{{x:.3g}} us<br>"
            f"p90 {r['p90_us']:.3g} us, p99 {r['p99_us']:.3g} us",
            scale_text(r["p50_us"] * 1e3, TIME),
        )
        for r in results
    ]
    return panels_figure({"": rows}, TIME, 1e3, "Round trip (log scale)", note)


def throughput_figure(results: list[Throughput], note: str) -> go.Figure:
    """One panel per item size and reader count: items received per second.

    The line runs from the slowest to the fastest repeat. A row from an older
    run, without those two values, is drawn as a dot alone.
    """
    panels: dict[str, list[Row]] = {}
    for r in results:
        title = f"{r['item']}, {r['readers']} reader{'s' * (r['readers'] != 1)}"
        name = r["contender"].split(" (")[0]
        received = r["received_per_s"]
        low = r.get("received_per_s_min", received)
        high = r.get("received_per_s_max", received)
        spread = f"<br>min {low:,.0f}/s, max {high:,.0f}/s" if low != high else ""
        panels.setdefault(title, []).append(
            Row(
                name,
                low,
                received,
                high,
                f"{name}<br>received %{{x:,.0f}}/s{spread}"
                f"<br>sent {r['sent_per_s']:,.0f}/s<br>missed {r['missed']}",
                scale_text(received, RATE),
            )
        )
    return panels_figure(panels, RATE, 1.0, "Items per second (log scale)", note)


def matrix_figure(results: list[Matrix], note: str) -> go.Figure:
    """One panel per item size: the p50 latency, with a line from p50 to p99.

    A pairing without latency (the buffered reader) has nothing to place on the
    axis and is left out.
    """
    panels: dict[str, list[Row]] = {}
    for r in results:
        p50, p90, p99 = r["p50_us"], r["p90_us"], r["p99_us"]
        if p50 is None or p90 is None or p99 is None:
            continue
        name = f"{r['sender']} / {r['reader']}"
        panels.setdefault(r["item"], []).append(
            Row(
                name,
                p50,
                p50,
                p99,
                f"{name}<br>p50 %{{x:.3g}} us<br>p90 {p90:.3g} us, p99 {p99:.3g} us"
                f"<br>{r['items_per_s']:,.0f} items/s",
                scale_text(p50 * 1e3, TIME),
            )
        )
    return panels_figure(panels, TIME, 1e3, "Latency (log scale)", note)


def load(path: Path) -> Any:
    """The parsed JSON in `path`."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_charts(folder: Path) -> list[Path]:
    """Write plotly figure JSON for each chart whose results exist in `folder`.

    The files go to `folder/charts`: `ops.json`, `roundtrip.json`,
    `stream-throughput.json` and `stream-matrix.json`. A chart whose results
    are missing is named on standard error and skipped.
    """
    note = machine_note(folder)
    charts: dict[str, tuple[str, Callable[[Path], go.Figure]]] = {
        "ops.json": ("ops.json", lambda p: ops_figure(p, note)),
        "roundtrip.json": (
            "roundtrip.json",
            lambda p: roundtrip_figure(load(p), note),
        ),
        "stream-throughput.json": (
            "stream.json",
            lambda p: throughput_figure(load(p)["throughput"], note),
        ),
        "stream-matrix.json": (
            "stream.json",
            lambda p: matrix_figure(load(p)["matrix"], note),
        ),
    }
    out = folder / "charts"
    out.mkdir(exist_ok=True)
    written = []
    for name, (source, build) in charts.items():
        if not (folder / source).exists():
            print(f"skipped {name}: no {source}", file=sys.stderr)
            continue
        path = out / name
        path.write_text(build(folder / source).to_json() + "\n", encoding="utf-8")
        written.append(path)
    return written
