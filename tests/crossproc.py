"""Read and write a box from a spawned process, for tests that check a value survives the trip."""

import multiprocessing as mp
from typing import Any

from sharedbox import SharedBox


def snapshot(box: SharedBox, results: "mp.Queue[dict[str, Any]]") -> None:
    results.put(box.snapshot())
    box.close()


def update(box: SharedBox, values: dict[str, Any]) -> None:
    box.update(**values)
    box.close()


def snapshot_in_child(box: SharedBox) -> dict[str, Any]:
    """Return the snapshot a spawned process takes of `box`, which it receives pickled."""
    context = mp.get_context("spawn")
    results: mp.Queue[dict[str, Any]] = context.Queue()
    child = context.Process(target=snapshot, args=(box, results))
    child.start()
    try:
        return results.get(timeout=60)
    finally:
        child.join(60)


def update_in_child(box: SharedBox, **values: Any) -> None:
    """Have a spawned process write `values` to `box` with `update`."""
    child = mp.get_context("spawn").Process(target=update, args=(box, values))
    child.start()
    child.join(60)
    assert child.exitcode == 0
