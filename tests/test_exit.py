import multiprocessing as mp
import subprocess
import sys
import threading
import time
from typing import Annotated

import numpy as np
import pytest

from sharedbox import DType, Shape, SharedBox

KEPT = """
class Kept(SharedBox):
    count: int
    payload: Annotated[bytes, Capacity(16)]


box = Kept.create(sys.argv[1], 1, b"x")
Kept.unlink(sys.argv[1])
"""

KEPT_PAST_EXIT = (
    """
import ctypes
import sys
from typing import Annotated

from sharedbox import Capacity, SharedBox
"""
    + KEPT
    + """
if sys.argv[2] == "closed":
    box.close()
else:
    ctypes.pythonapi.Py_IncRef(ctypes.py_object(box))
ctypes.pythonapi.Py_IncRef(ctypes.py_object(Kept))
"""
)

USED_AFTER_RELEASE = (
    """
import sys
from typing import Annotated

from sharedbox import BoxClosedError, Capacity, SharedBox
from sharedbox._exit import release_native
"""
    + KEPT
    + """
release_native()
for action in (lambda: box.count, lambda: Kept.create(sys.argv[1] + "-new", 2, b"y")):
    try:
        action()
    except BoxClosedError as exc:
        print(type(exc).__name__, exc)
box.close()
print("closed", box.closed)
"""
)

STREAM_PAST_EXIT = """
import ctypes
import sys

from sharedbox import SharedStream

stream = SharedStream.create(int, sys.argv[1], capacity=4)
SharedStream.unlink(sys.argv[1])
sender = stream.sender()
reader = stream.reader()
sender.send(1)
for kept in (stream, sender, reader):
    ctypes.pythonapi.Py_IncRef(ctypes.py_object(kept))
"""


def run_child(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )


class Blob(SharedBox):
    tick: int = 0
    big: Annotated[np.ndarray, Shape((512, 512)), DType("float64")] = np.zeros(
        (512, 512)
    )


def exit_during_a_callback(name: str) -> None:
    """Return from the child while a callback is reading and closing fresh handles."""
    box = Blob.attach(name)
    started = threading.Event()

    def churn(new: int, old: int) -> None:
        started.set()
        end = time.monotonic() + 1
        while time.monotonic() < end:
            with Blob.attach(name) as fresh:
                _ = fresh.big

    box.events.tick.connect(churn)
    with Blob.attach(name) as writer:
        while not started.wait(0.05):
            writer.tick += 1


def test_a_process_exits_cleanly_while_a_callback_makes_native_calls(
    unique_name: str,
) -> None:
    """Check that spawned processes exit with code 0 when a callback is inside native calls at exit."""
    context = mp.get_context("spawn")
    with Blob.create(unique_name):
        children = [
            context.Process(target=exit_during_a_callback, args=(unique_name,))
            for _ in range(5)
        ]
        for child in children:
            child.start()
        for child in children:
            child.join(60)
            if child.is_alive():
                child.kill()
                child.join(10)
        Blob.unlink(unique_name)
    exitcodes = [child.exitcode for child in children]
    assert exitcodes == [0] * len(children), f"exit codes: {exitcodes}"


@pytest.mark.parametrize("state", ["closed", "open"])
def test_a_class_kept_past_exit_leaks_nothing(unique_name: str, state: str) -> None:
    """Check that a box class, and a box closed or left open, kept alive past finalization leak no native objects."""
    done = run_child(KEPT_PAST_EXIT, unique_name, state)
    assert "nanobind: leaked" not in done.stderr
    assert done.returncode == 0, done.stderr


def test_a_stream_kept_past_exit_leaks_nothing(unique_name: str) -> None:
    """Check that a stream, its sender and a reader kept alive past finalization leak no native objects."""
    done = run_child(STREAM_PAST_EXIT, unique_name)
    assert "nanobind: leaked" not in done.stderr
    assert done.returncode == 0, done.stderr


def test_a_box_used_after_release_raises(unique_name: str) -> None:
    """Check that reading a box and creating one after the exit handler raise BoxClosedError."""
    done = run_child(USED_AFTER_RELEASE, unique_name)
    released = (
        "is unavailable: sharedbox released its native objects at interpreter exit"
    )
    assert (done.returncode, done.stdout.splitlines()) == (
        0,
        [
            f"BoxClosedError Kept.count {released}",
            f"BoxClosedError Kept {released}",
            "closed True",
        ],
    ), done.stderr
