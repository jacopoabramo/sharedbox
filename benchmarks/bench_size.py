"""Wheel and extension-module size, read straight from the zip.

uv run python benchmarks/bench_size.py                 # dist/*.whl, wheelhouse/*.whl
uv run python benchmarks/bench_size.py dist/*.whl       # explicit wheels
uv run python benchmarks/bench_size.py --json sizes.json
uv run python benchmarks/bench_size.py --markdown
"""

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import TypedDict

NATIVE_RE = re.compile(r"sharedbox/_native.*\.(pyd|so)$")


class WheelSize(TypedDict):
    wheel: str
    tag: str
    wheel_size: int
    native_path: str | None
    native_compressed: int | None
    native_uncompressed: int | None


def wheel_tag(name: str) -> str:
    # A wheel filename is {name}-{version}-{python}-{abi}-{platform}.whl;
    # the tag is everything from the python token onward.
    return "-".join(Path(name).stem.split("-")[2:])


def measure(wheel: Path) -> WheelSize:
    native_path: str | None = None
    native_compressed: int | None = None
    native_uncompressed: int | None = None
    with zipfile.ZipFile(wheel) as zf:
        for info in zf.infolist():
            if NATIVE_RE.search(info.filename):
                native_path = info.filename
                native_compressed = info.compress_size
                native_uncompressed = info.file_size
                break
    return WheelSize(
        wheel=wheel.name,
        tag=wheel_tag(wheel.name),
        wheel_size=wheel.stat().st_size,
        native_path=native_path,
        native_compressed=native_compressed,
        native_uncompressed=native_uncompressed,
    )


def default_wheels() -> list[Path]:
    wheels = sorted(Path("dist").glob("*.whl")) + sorted(
        Path("wheelhouse").glob("*.whl")
    )
    return wheels


def to_markdown(sizes: list[WheelSize]) -> str:
    lines = [
        "| wheel | wheel size | native (uncompressed) | native (compressed) |",
        "| --- | --- | --- | --- |",
    ]
    for s in sizes:
        native = (
            f"{s['native_uncompressed']:,} B"
            if s["native_uncompressed"] is not None
            else "n/a"
        )
        native_c = (
            f"{s['native_compressed']:,} B"
            if s["native_compressed"] is not None
            else "n/a"
        )
        lines.append(f"| {s['tag']} | {s['wheel_size']:,} B | {native} | {native_c} |")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "wheels", nargs="*", help="wheel files (default: dist/*.whl, wheelhouse/*.whl)"
    )
    parser.add_argument(
        "--json", metavar="PATH", help="write machine-readable output to PATH"
    )
    parser.add_argument(
        "--markdown", action="store_true", help="print a Markdown table"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    wheels = [Path(w) for w in args.wheels] if args.wheels else default_wheels()
    if not wheels:
        print("no wheels found", file=sys.stderr)
        return 1

    sizes = [measure(w) for w in wheels]

    if args.json:
        Path(args.json).write_text(json.dumps(sizes, indent=2))

    if args.markdown:
        print(to_markdown(sizes))
    else:
        for s in sizes:
            print(
                f"{s['tag']}: wheel {s['wheel_size']:,} B, "
                f"native {s['native_uncompressed']:,} B "
                f"({s['native_compressed']:,} B compressed)"
                if s["native_uncompressed"] is not None
                else f"{s['tag']}: wheel {s['wheel_size']:,} B, native not found"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
