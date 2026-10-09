"""Compare the stream benchmarks of the base and the head of a pull request.

Reads `<side>-<round>.json` files, where side is `base` or `head`, from the
folder given as the only argument. Each file holds the `throughput` and
`matrix` rows that `benchbox stream --json` writes.

Prints the machine description, then one Markdown table for throughput
(`received_per_s`) and one for the sync and async matrix (`items_per_s` and
`p50_us`). Each cell is the median over the rounds of one side, followed by
`head / base`. A row or value present on one side only shows `-`.

Run it in the head environment, in CI.
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from sharedbox.benchmarks._app import machine

Key = tuple[str, ...]
Samples = dict[tuple[Key, str], list[float]]


def collect(folder: Path, name: str, key: tuple[str, ...], metric: str) -> Samples:
    """Gather `metric` of each row of the table `name`, by row key and side."""
    samples: Samples = defaultdict(list)
    for path in sorted(folder.glob("*.json")):
        side = path.name.split("-")[0]
        for row in json.loads(path.read_text())[name]:
            if row[metric] is not None:
                samples[tuple(str(row[k]) for k in key), side].append(row[metric])
    return samples


def median(samples: Samples, row: Key, side: str) -> float | None:
    """The median of one row on one side, or None when that side has none."""
    values = samples.get((row, side))
    return statistics.median(values) if values else None


def show(value: float | None) -> str:
    return "-" if value is None else f"{value:,.1f}"


def print_table(
    folder: Path,
    title: str,
    name: str,
    key: tuple[str, ...],
    metrics: dict[str, str],
) -> None:
    """Print one table: the key columns, then base, head and the ratio per metric."""
    found = {m: collect(folder, name, key, m) for m in metrics}
    columns = [*key]
    for label in metrics.values():
        columns += [f"{label} base", f"{label} head", f"{label} head / base"]
    print(f"### {title}")
    print()
    print("| " + " | ".join(columns) + " |")
    print("| " + " | ".join("---" for _ in columns) + " |")
    for row in sorted({row for samples in found.values() for row, _ in samples}):
        out = list(row)
        for samples in found.values():
            base = median(samples, row, "base")
            head = median(samples, row, "head")
            ratio = f"{head / base:.3f}" if base and head else "-"
            out += [show(base), show(head), ratio]
        print("| " + " | ".join(out) + " |")
    print()


def main(folder: Path) -> None:
    print(machine())
    print()
    print_table(
        folder,
        "Throughput, items received per second",
        "throughput",
        ("contender", "item", "readers"),
        {"received_per_s": "received/s"},
    )
    print_table(
        folder,
        "Sync and async matrix",
        "matrix",
        ("sender", "reader", "item"),
        {"items_per_s": "items/s", "p50_us": "p50 us"},
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
