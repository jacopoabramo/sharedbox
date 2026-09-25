"""The ``sharedbox-bench`` commands."""

import json
import os
import platform
import subprocess
import sys
import sysconfig
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path
from typing import Annotated

import typer

from sharedbox.benchmarks import roundtrip, size
from sharedbox.benchmarks.cli import INSTALL_HINT

app = typer.Typer(
    help="Measure sharedbox on this machine.",
    no_args_is_help=True,
    add_completion=False,
)

JsonOption = Annotated[
    Path | None,
    typer.Option("--json", metavar="FILE", help="Also write the results to FILE."),
]


def run_ops(
    fast: bool, pattern: str | None, output: Path | None, extra: list[str]
) -> int:
    if find_spec("pyperf") is None:
        raise SystemExit(INSTALL_HINT)
    cmd = [sys.executable, "-m", "sharedbox.benchmarks.ops"]
    if fast:
        cmd.append("--fast")
    if pattern is not None:
        cmd += ["--filter", pattern]
    if output is not None:
        cmd += ["-o", str(output)]
    return subprocess.call([*cmd, *extra])


@app.command(
    "ops",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def ops_command(
    ctx: typer.Context,
    fast: Annotated[
        bool, typer.Option("--fast", help="Fewer runs: quicker, less accurate.")
    ] = False,
    pattern: Annotated[
        str | None,
        typer.Option(
            "--filter",
            metavar="PATTERN",
            help='Only benchmarks whose "operation/contender" name matches '
            "this glob pattern.",
        ),
    ] = None,
    output: JsonOption = None,
) -> None:
    """Time single operations against the standard library, with pyperf.

    Arguments after -- go to pyperf unchanged.
    """
    raise typer.Exit(run_ops(fast, pattern, output, ctx.args))


@app.command("roundtrip")
def roundtrip_command(
    samples: Annotated[
        int, typer.Option(min=1, help="Round trips timed per contender.")
    ] = roundtrip.SAMPLES,
    warmup: Annotated[
        int, typer.Option(min=0, help="Round trips discarded before timing.")
    ] = roundtrip.WARMUP,
    timeout: Annotated[
        float, typer.Option(min=0.001, help="Seconds to wait for one answer.")
    ] = roundtrip.TIMEOUT,
) -> None:
    """Time a change sent to another process and answered back."""
    results = roundtrip.run(roundtrip.Options(samples, warmup, timeout))
    typer.echo(roundtrip.to_text(results))


@app.command("size")
def size_command(
    wheels: Annotated[
        list[Path] | None,
        typer.Argument(help="Wheels to measure; default dist/*.whl, wheelhouse/*.whl."),
    ] = None,
    output: JsonOption = None,
    markdown: Annotated[
        bool, typer.Option("--markdown", help="Print a Markdown table.")
    ] = False,
) -> None:
    """Report the size of wheels and of the extension module inside them."""
    argv = [str(wheel) for wheel in wheels or []]
    if output is not None:
        argv += ["--json", str(output)]
    if markdown:
        argv.append("--markdown")
    raise typer.Exit(size.main(argv))


def cpu_name() -> str:
    if sys.platform == "win32":
        import winreg

        key = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
            return str(winreg.QueryValueEx(handle, "ProcessorNameString")[0]).strip()
    else:
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.partition(":")[2].strip()
        except OSError:
            pass
        return platform.processor() or platform.machine()


def machine() -> str:
    build = "free-threaded" if sysconfig.get_config_var("Py_GIL_DISABLED") else "GIL"
    python = f"{platform.python_implementation()} {platform.python_version()}"
    return "\n".join(
        [
            f"- OS: {platform.platform()}",
            f"- CPU: {cpu_name()}, {os.cpu_count()} logical cores",
            f"- Python: {python} ({build})",
            f"- sharedbox: {version('sharedbox')}",
        ]
    )


def ops_markdown(path: Path) -> str:
    import pyperf

    lines = ["| benchmark | mean | std dev |", "| --- | --- | --- |"]
    for bench in pyperf.BenchmarkSuite.load(str(path)).get_benchmarks():
        mean, stdev = bench.format_values([bench.mean(), bench.stdev()])
        lines.append(f"| {bench.get_name()} | {mean} | {stdev} |")
    return "\n".join(lines)


@app.command("all")
def all_command(
    out: Annotated[
        Path,
        typer.Option(metavar="DIR", help="Directory for the JSON and summary.md."),
    ],
    fast: Annotated[
        bool, typer.Option("--fast", help="Pass --fast to the ops benchmarks.")
    ] = False,
) -> None:
    """Run ops, roundtrip and size (when wheels are found) and summarise them."""
    out.mkdir(parents=True, exist_ok=True)
    ops_json = out / "ops.json"
    # pyperf refuses to write over an existing file.
    ops_json.unlink(missing_ok=True)
    if code := run_ops(fast, None, ops_json, []):
        raise typer.Exit(code)
    results = roundtrip.run(roundtrip.Options())
    (out / "roundtrip.json").write_text(json.dumps(results, indent=2))
    sections = [
        "# sharedbox benchmarks",
        machine(),
        "## Single operations",
        ops_markdown(ops_json),
        "## Round trips",
        roundtrip.to_markdown(results),
    ]
    if wheels := size.default_wheels():
        sizes = [size.measure(wheel) for wheel in wheels]
        (out / "size.json").write_text(json.dumps(sizes, indent=2))
        sections += ["## Wheel size", size.to_markdown(sizes)]
    summary = "\n\n".join(sections) + "\n"
    (out / "summary.md").write_text(summary, encoding="utf-8")
    typer.echo(summary)
