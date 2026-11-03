"""Run a box or a stream end in another process: read and write a box from a spawned process, or fork."""

import contextlib
import multiprocessing as mp
import sys
from collections.abc import Callable, Generator, Mapping
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event as EventType
from typing import Any

from sharedbox import AlreadyServedError, SharedBox


def snapshot(box: SharedBox, results: "mp.Queue[dict[str, Any]]") -> None:
    results.put(box.snapshot())
    box.close()


def update(box: SharedBox, values: dict[str, Any]) -> None:
    box.update(**values)
    box.close()


def read_and_update_metadata(
    box: SharedBox, value: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Read the metadata of `box`, write `value` as its next metadata version, close the box and return what was read."""
    seen = box.metadata
    box.update(metadata=value)
    box.close()
    return None if seen is None else dict(seen)


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


def fork(target: Callable[..., object], *args: object) -> BaseProcess:
    """Start `target(*args)` in a forked daemon process."""
    # mypy checks this module as Windows code too; the else form lets it skip the
    # "fork" context, which Windows lacks, without an unreachable-code error.
    if sys.platform == "win32":
        raise NotImplementedError("Windows has no fork")
    else:
        process = mp.get_context("fork").Process(target=target, args=args, daemon=True)
        process.start()
        return process


def serve(
    box: SharedBox,
    handlers: dict[str, Callable[..., Any]],
    ready: EventType,
    stop: Connection,
) -> None:
    for name, handler in handlers.items():
        getattr(box, name).serve(handler)
    ready.set()
    # The parent closes its end to stop the child: an Event cannot be set once a waiter was killed.
    with contextlib.suppress(EOFError):
        stop.recv()
    box.close()


def serve_once(box: SharedBox, name: str) -> bool:
    """Return whether `box` could serve its call field `name`, stopping at once if so."""
    try:
        getattr(box, name).serve(len)
    except AlreadyServedError:
        return False
    finally:
        box.close()
    return True


@contextlib.contextmanager
def serving_in_child(
    box: SharedBox, **handlers: Callable[..., Any]
) -> Generator[BaseProcess, None, None]:
    """Serve each call field named in `handlers` in a spawned process until the block ends."""
    context = mp.get_context("spawn")
    ready = context.Event()
    stop, stopping = context.Pipe(duplex=False)
    child = context.Process(target=serve, args=(box, handlers, ready, stop))
    child.start()
    try:
        while not ready.wait(0.1):
            assert child.is_alive(), "the child exited before it served"
        yield child
    finally:
        stopping.close()
        child.join(10)
        if child.is_alive():
            child.kill()
            child.join(10)


def put_result(
    function: Callable[..., object],
    args: tuple[object, ...],
    results: "mp.Queue[object]",
) -> None:
    results.put(function(*args))


def call_in_child(function: Callable[..., object], *args: object) -> object:
    """Return what `function(*args)` gives in a spawned process, which receives both pickled."""
    context = mp.get_context("spawn")
    results: mp.Queue[object] = context.Queue()
    child = context.Process(target=put_result, args=(function, args, results))
    child.start()
    try:
        return results.get(timeout=60)
    finally:
        child.join(60)
