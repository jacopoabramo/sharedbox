import multiprocessing as mp
import os
import subprocess
import sys
import threading
import time
from typing import Annotated

import numpy as np

from sharedbox import DType, Shape, SharedBox

# A daemon thread still running at exit keeps the class, and with it the extension's objects, alive
# past finalization.
HELD_AT_EXIT = """
import threading, time
from sharedbox import SharedBox

class Held(SharedBox):
    version: int = 0

threading.Thread(target=lambda cls: time.sleep(60), args=(Held,), daemon=True).start()
"""


class Blob(SharedBox):
    tick: int = 0
    big: Annotated[np.ndarray, Shape(512, 512), DType("float64")] = np.zeros((512, 512))


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


def exit_holding_a_box_class(env: dict[str, str]) -> str:
    """Return what a process prints to stderr when it exits while a thread holds a box class."""
    return subprocess.run(
        [sys.executable, "-c", HELD_AT_EXIT],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stderr


def test_exit_prints_no_leak_report_by_default() -> None:
    """Print nothing at exit about objects a running thread still holds, without SHAREDBOX_LEAK_WARNINGS."""
    env = {k: v for k, v in os.environ.items() if k != "SHAREDBOX_LEAK_WARNINGS"}
    assert "nanobind: leaked" not in exit_holding_a_box_class(env)


def test_sharedbox_leak_warnings_turns_the_leak_report_on() -> None:
    """Print nanobind's leak report at exit when SHAREDBOX_LEAK_WARNINGS is set."""
    env = {**os.environ, "SHAREDBOX_LEAK_WARNINGS": "1"}
    assert "nanobind: leaked" in exit_holding_a_box_class(env)
