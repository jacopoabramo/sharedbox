import multiprocessing as mp
import time
from collections.abc import Callable

import pytest
from stress_helpers import scaled

from sharedbox import SharedBox

pytestmark = pytest.mark.stress

BOXES = 16
OPEN: list[SharedBox] = []


class Tick(SharedBox):
    value: int = 0


def exit_with_watchers_running(names: list[str]) -> None:
    """Attach every box and listen to it, then exit with the boxes still open."""
    for name in names:
        box = Tick.attach(name)
        box.events.value.connect(lambda new, old: None)
        OPEN.append(box)


def test_processes_exit_cleanly_with_watcher_threads_running(
    unique_name: str, report: Callable[[dict[str, object]], None]
) -> None:
    """Check that a spawned process whose boxes are left open with running watcher threads exits with code 0."""
    rounds = scaled(100)
    context = mp.get_context("spawn")
    names = [f"{unique_name}-{i}" for i in range(BOXES)]
    boxes = [Tick.create(name) for name in names]
    exitcodes: list[int | None] = []
    start = time.monotonic()
    try:
        for _ in range(rounds):
            child = context.Process(target=exit_with_watchers_running, args=(names,))
            child.start()
            child.join(30)
            if child.is_alive():
                child.kill()
                child.join(10)
            exitcodes.append(child.exitcode)
    finally:
        for box in boxes:
            box.close()
            Tick.unlink(box.name)
    failed = [code for code in exitcodes if code != 0]
    report(
        {
            "rounds": rounds,
            "boxes": BOXES,
            "seconds": time.monotonic() - start,
            "failed": len(failed),
            "exitcodes": failed,
        }
    )
    assert failed == []
