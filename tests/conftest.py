import contextlib
import json
import os
import sys
import uuid
from collections.abc import Callable, Iterator
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
    return None


@pytest.fixture
def unique_name() -> Iterator[str]:
    name = f"sbtest-{uuid.uuid4().hex[:16]}"
    yield name
    if sys.platform.startswith("linux"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f"/dev/shm/sharedbox.{name}")


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
