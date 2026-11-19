"""Compare the `benchbox ops` timings of the base and the head of a pull request.

Reads `<side>-<round>-<rows>.json` files, where side is `base` or `head`,
from the folder given as the only argument. Each file is what
`benchbox ops --json` writes for one group of rows.

Prints the machine description, then one Markdown table: each side's median
over its rounds, followed by `head / base`. The ratio of each round is
taken first and the table shows the median of those ratios, because the base
and the head of one round ran minutes apart, so a runner that drifts between
rounds moves it less. A row present on one side only shows `-`.

Run it in the head environment, in CI.
"""

import statistics
import sys
from pathlib import Path

import pyperf

from sharedbox.benchmarks._app import machine


def main(folder: Path) -> None:
    """Print the table of the result files in `folder`."""
    means: dict[tuple[str, str], dict[str, float]] = {}
    for path in sorted(folder.glob("*.json")):
        side, round_ = path.name.split("-")[:2]
        for bench in pyperf.BenchmarkSuite.load(str(path)).get_benchmarks():
            means.setdefault((bench.get_name(), side), {})[round_] = bench.mean()
    print(
        "### benchbox ops on Windows cp311, median of three rounds, list, set and dict rows one round"
    )
    print()
    print(
        "head / base is the median of each round's ratio, so a runner that drifts between rounds moves it less."
    )
    print()
    print(
        "A pull request skips the 64k set and dict rows; start the workflow by hand for them."
    )
    print()
    print(machine())
    print()
    print("| benchmark | base | head | head / base |")
    print("| --- | --- | --- | --- |")
    for name in sorted({name for name, _ in means}):
        base = means.get((name, "base"), {})
        head = means.get((name, "head"), {})
        cells = [
            f"{statistics.median(v.values()) * 1e9:.1f} ns" if v else "-"
            for v in (base, head)
        ]
        ratios = [head[r] / base[r] for r in base if r in head and base[r]]
        ratio = f"{statistics.median(ratios):.3f}" if ratios else "-"
        print(f"| {name} | {cells[0]} | {cells[1]} | {ratio} |")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
