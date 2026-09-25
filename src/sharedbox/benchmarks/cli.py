"""Entry point of the ``benchbox`` command."""

from importlib.util import find_spec

INSTALL_HINT = "benchbox needs Typer and pyperf: install sharedbox[benchmarks]"


def main() -> None:
    """Run the benchmark commands; exit with an install hint when Typer is missing."""
    if find_spec("typer") is None:
        raise SystemExit(INSTALL_HINT)
    from sharedbox.benchmarks._app import app

    app(prog_name="benchbox")
