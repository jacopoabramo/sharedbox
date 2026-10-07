import multiprocessing as mp
import time
from collections.abc import Callable
from typing import Annotated

import numpy as np
import pytest
from stress_helpers import scale

from sharedbox import DType, Shape, SharedBox

pytestmark = pytest.mark.stress


class Uniform(SharedBox):
    image: Annotated[np.ndarray, Shape(1024, 1024), DType("float32")] = np.zeros(
        (1024, 1024), np.float32
    )
    small: Annotated[np.ndarray, Shape(64), DType("int64")] = np.zeros(64, np.int64)


def write_uniform(name: str, seconds: float) -> None:
    box = Uniform.attach(name)
    deadline = time.monotonic() + seconds
    k = 0
    while time.monotonic() < deadline:
        k += 1
        box.update(
            image=np.full((1024, 1024), k, np.float32), small=np.full(64, k, np.int64)
        )
    box.close()


def test_read_into_never_mixes_two_writes(
    unique_name: str, report: Callable[[dict[str, object]], None]
) -> None:
    """Fill out with a single write in every read while two processes keep writing."""
    seconds = 20 * scale()
    image = np.empty((1024, 1024), np.float32)
    small = np.empty(64, np.int64)
    with Uniform.create(unique_name) as box:
        context = mp.get_context("spawn")
        writers = [
            context.Process(target=write_uniform, args=(unique_name, seconds))
            for _ in range(2)
        ]
        for writer in writers:
            writer.start()
        reads = torn = 0
        while any(writer.is_alive() for writer in writers):
            box.read_into("image", image)
            box.read_into("small", small)
            reads += 1
            torn += image.min() != image.max() or small.min() != small.max()
        for writer in writers:
            writer.join(60)
            assert writer.exitcode == 0
        report({"seconds": seconds, "reads": reads, "torn": torn})
        assert reads > 0
        assert torn == 0
    Uniform.unlink(unique_name)
