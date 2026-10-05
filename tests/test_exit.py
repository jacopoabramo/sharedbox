import multiprocessing as mp
import threading
import time
from typing import Annotated

import numpy as np

from sharedbox import DType, Shape, SharedBox


class Blob(SharedBox):
    tick: int = 0
    big: Annotated[np.ndarray, Shape(512, 512), DType("float64")] = np.zeros(
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
                fresh.big

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
