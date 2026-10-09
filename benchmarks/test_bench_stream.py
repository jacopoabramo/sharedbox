import os
from collections.abc import Iterator
from itertools import count
from typing import Any

import numpy as np
import pytest
from pytest_codspeed import BenchmarkFixture

from sharedbox import SharedStream
from sharedbox.benchmarks.stream import Small, zeros

NAMES = count()


@pytest.fixture
def ends() -> Iterator[tuple[Any, Any, Small, dict[str, np.ndarray]]]:
    name = f"bench-codspeed-{os.getpid()}-{next(NAMES)}"
    with SharedStream.create(Small, name, capacity=64) as stream:
        reader = stream.reader(start="oldest")
        with stream.sender() as sender:
            yield sender, reader, Small(0, zeros("1 KiB")), {"data": zeros("1 KiB")}
    SharedStream.unlink(name)


def test_send_and_receive_into(
    benchmark: BenchmarkFixture,
    ends: tuple[Any, Any, Small, dict[str, np.ndarray]],
) -> None:
    sender, reader, item, out = ends
    benchmark(lambda: (sender.send(item), reader.receive_into(out)))
