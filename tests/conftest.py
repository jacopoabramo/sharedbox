import contextlib
import json
import os
import sys
import uuid
from collections.abc import Callable, Iterator
from importlib.util import find_spec
from pathlib import Path

import pytest
from hypothesis import settings

# A spawned child inherits this from its parent's environment, so a fixed
# segment name built from it stays the same across a run's own processes
# while differing from any other run's.
TEST_RUN = os.environ.setdefault("SHAREDBOX_TEST_RUN", uuid.uuid4().hex[:8])

# ci keeps the default suite fast; the stress job asks for thorough with --hypothesis-profile.
settings.register_profile("ci", max_examples=50, deadline=None, derandomize=False)
settings.register_profile("thorough", max_examples=2000, deadline=None)
settings.load_profile("ci")


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    # A deselected stress module is still imported, and its box classes then outlive the
    # extension at exit, which nanobind reports as leaked instances.
    if (
        collection_path.name == "stress"
        and config.getoption("markexpr") == "not stress"
    ):
        return True
    # Its class names itself in an unquoted annotation, which fails to evaluate before 3.14.
    if collection_path.name == "test_refs_lazy.py" and sys.version_info < (3, 14):
        return True
    # The wheel tests in CI install neither, since they come with the benchmarks extra.
    if collection_path.name == "test_benchbox_plot.py" and (
        find_spec("matplotlib") is None or find_spec("pyperf") is None
    ):
        return True
    # attrs, msgspec and ml_dtypes are dev dependencies that may lack wheels for an interpreter
    # under test; torch is not a dependency at all.
    optional = {
        "test_types_records_attrs.py": "attrs",
        "test_types_records_msgspec.py": "msgspec",
        "test_types_arrays_bfloat16.py": "ml_dtypes",
        "test_types_arrays_torch.py": "torch",
    }
    if (
        collection_path.name in optional
        and find_spec(optional[collection_path.name]) is None
    ):
        return True
    return None


@pytest.fixture
def unique_name() -> Iterator[str]:
    name = f"sbtest-{uuid.uuid4().hex[:16]}"
    yield name
    if sys.platform.startswith("linux"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f"/dev/shm/sharedbox.{name}")


@pytest.fixture
def names(unique_name: str) -> Iterator[Callable[[str], str]]:
    """Box names made from `unique_name`; on Linux each one is removed afterwards."""
    made: list[str] = []

    def name(suffix: str) -> str:
        made.append(f"{unique_name}-{suffix}")
        return made[-1]

    yield name
    if sys.platform.startswith("linux"):
        for each in made:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(f"/dev/shm/sharedbox.{each}")


STRESS_OUT = Path(__file__).resolve().parent.parent / "build" / "stress"
STRESS_TABLES: list[tuple[str, dict[str, object]]] = []


@pytest.fixture
def report(request: pytest.FixtureRequest) -> Callable[[dict[str, object]], None]:
    """Writes a stress test's numbers to build/stress/<test>.json and to the run's summary."""

    def write(results: dict[str, object]) -> None:
        results = {"platform": sys.platform, **results}
        STRESS_OUT.mkdir(parents=True, exist_ok=True)
        path = STRESS_OUT / f"{request.node.name}.json"
        path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        STRESS_TABLES.append((request.node.name, results))

    return write


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    for name, results in STRESS_TABLES:
        terminalreporter.write_sep("-", name)
        for key, value in results.items():
            terminalreporter.write_line(f"{key:>30}  {value}")
