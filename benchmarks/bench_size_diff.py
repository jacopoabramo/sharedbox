"""Markdown table of wheel size changes against a baseline.

Reads ``bench_size.py --json`` output (one or more files, searched
recursively) from two directories and prints a Markdown table comparing
wheel sizes by tag. With no baseline directory, or an empty one, prints a
one-line note instead.

    uv run python benchmarks/bench_size_diff.py sizes/this sizes/main
"""

import argparse
import json
import sys
from pathlib import Path
from typing import TypedDict


class DiffRow(TypedDict):
    tag: str
    current: int
    baseline: int | None


def load(root: Path) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for path in root.rglob("*.json"):
        for entry in json.loads(path.read_text()):
            sizes[entry["tag"]] = entry["wheel_size"]
    return sizes


def diff_rows(this: dict[str, int], main: dict[str, int]) -> list[DiffRow]:
    if not main:
        return []
    return [
        DiffRow(tag=tag, current=this[tag], baseline=main.get(tag))
        for tag in sorted(this)
    ]


def to_markdown(rows: list[DiffRow]) -> str:
    lines = ["## Wheel size vs main"]
    if not rows:
        lines.append("No baseline size data from main; nothing to compare.")
        return "\n".join(lines)

    lines += ["| wheel | this run | main | change |", "| --- | --- | --- | --- |"]
    for row in rows:
        current, baseline = row["current"], row["baseline"]
        if baseline is None:
            lines.append(f"| {row['tag']} | {current:,} B | n/a | n/a |")
        else:
            delta = current - baseline
            sign = "+" if delta >= 0 else ""
            lines.append(
                f"| {row['tag']} | {current:,} B | {baseline:,} B | {sign}{delta:,} B |"
            )
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "this_dir", type=Path, help="directory of this run's size JSON files"
    )
    parser.add_argument(
        "main_dir",
        type=Path,
        nargs="?",
        help="directory of the baseline's size JSON files",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    this = load(args.this_dir)
    main_sizes = load(args.main_dir) if args.main_dir and args.main_dir.exists() else {}

    print(to_markdown(diff_rows(this, main_sizes)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
