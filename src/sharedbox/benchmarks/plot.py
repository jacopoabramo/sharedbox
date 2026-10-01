"""Charts of the results of `benchbox all`, as SVG files for light and dark pages."""

import json
from pathlib import Path
from typing import NamedTuple

import matplotlib
import pyperf
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, NullLocator

from sharedbox.benchmarks.roundtrip import Result


class Theme(NamedTuple):
    """Colours of one chart variant; `accent` marks sharedbox, `other` the rest."""

    name: str
    text: str
    muted: str
    grid: str
    accent: str
    other: str


THEMES = (
    Theme("light", "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6", "#8f8e89"),
    Theme("dark", "#ffffff", "#c3c2b7", "#383835", "#3987e5", "#8f8e89"),
)
BAR_IN = 0.24


def ops_medians(path: Path) -> dict[str, list[tuple[str, float]]]:
    """Return each contender's median in nanoseconds, by operation, in run order.

    Operations timed for one contender only are left out, since they compare
    nothing.
    """
    groups: dict[str, list[tuple[str, float]]] = {}
    for bench in pyperf.BenchmarkSuite.load(str(path)).get_benchmarks():
        operation, _, contender = bench.get_name().partition("/")
        groups.setdefault(operation, []).append((contender, bench.median() * 1e9))
    return {op: rows for op, rows in groups.items() if len(rows) > 1}


def style(ax: Axes, theme: Theme) -> None:
    """Make the axes recessive: no box, light vertical grid, muted ticks."""
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(theme.grid)
    ax.tick_params(colors=theme.muted, length=0)
    ax.grid(axis="x", which="major", color=theme.grid, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_facecolor("none")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    ax.xaxis.set_minor_locator(NullLocator())


def ops_figure(groups: dict[str, list[tuple[str, float]]], theme: Theme) -> Figure:
    """Draw one panel of horizontal bars per operation, on one shared log axis."""
    rows = sum(len(group) for group in groups.values())
    fig = Figure(figsize=(8, rows * BAR_IN + len(groups) * 0.45), layout="constrained")
    axes = fig.subplots(
        len(groups),
        1,
        sharex=True,
        squeeze=False,
        gridspec_kw={"height_ratios": [len(group) for group in groups.values()]},
    )[:, 0]
    for ax, (operation, group) in zip(axes, groups.items(), strict=True):
        names = [name for name, _ in group]
        times = [ns for _, ns in group]
        mine = [name.startswith("SharedBox") for name in names]
        ys = range(len(group))
        ax.barh(
            ys,
            times,
            height=0.7,
            color=[theme.accent if m else theme.other for m in mine],
        )
        ax.set_yticks(ys, names)
        ax.invert_yaxis()
        ax.set_xscale("log")
        ax.set_title(operation, loc="left", color=theme.text, fontweight="bold")
        for y, ns, m in zip(ys, times, mine, strict=True):
            if m:
                ax.annotate(
                    f"{ns:.0f} ns",
                    (ns, y),
                    xytext=(4, 0),
                    textcoords="offset points",
                    va="center",
                    color=theme.muted,
                )
        style(ax, theme)
    axes[-1].set_xlabel("Time per operation (ns, log scale)", color=theme.muted)
    return fig


def roundtrip_figure(results: list[Result], theme: Theme) -> Figure:
    """Draw one row per contender: the p50 to p99 range, with p50, p90 and p99 marked."""
    fig = Figure(figsize=(8, len(results) * 0.45 + 1.0), layout="constrained")
    ax = fig.subplots()
    marks = (("o", 8, "p50"), ("D", 6, "p90"), ("s", 6, "p99"))
    for y, result in enumerate(results):
        colour = (
            theme.accent if result["contender"].startswith("SharedBox") else theme.other
        )
        cuts = (result["p50_us"], result["p90_us"], result["p99_us"])
        ax.plot([cuts[0], cuts[2]], [y, y], color=colour, linewidth=2)
        for us, (marker, size, _) in zip(cuts, marks, strict=True):
            ax.plot(us, y, marker=marker, markersize=size, color=colour)
    ax.set_yticks(range(len(results)), [r["contender"] for r in results])
    ax.invert_yaxis()
    ax.set_xscale("log")
    ax.set_xlabel("Round trip (us, log scale)", color=theme.muted)
    key = [
        Line2D([], [], marker=marker, markersize=size, color=theme.muted, ls="none")
        for marker, size, _ in marks
    ]
    fig.legend(
        key,
        [label for _, _, label in marks],
        loc="outside upper right",
        ncols=len(marks),
        frameon=False,
        labelcolor=theme.muted,
    )
    style(ax, theme)
    return fig


def write_charts(folder: Path) -> list[Path]:
    """Write `ops-<theme>.svg` and `roundtrip-<theme>.svg` from the JSON in `folder`."""
    groups = ops_medians(folder / "ops.json")
    results: list[Result] = json.loads(
        (folder / "roundtrip.json").read_text(encoding="utf-8")
    )
    written = []
    # Text stays text, so pages can search it, and ids do not change between runs.
    with matplotlib.rc_context(
        {"svg.fonttype": "none", "svg.hashsalt": "sharedbox", "font.size": 9}
    ):
        for theme in THEMES:
            for stem, fig in (
                ("ops", ops_figure(groups, theme)),
                ("roundtrip", roundtrip_figure(results, theme)),
            ):
                path = folder / f"{stem}-{theme.name}.svg"
                fig.savefig(path, transparent=True, metadata={"Date": None})
                written.append(path)
    return written
