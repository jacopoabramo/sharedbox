import os
from collections.abc import Iterator
from itertools import count
from typing import Annotated

import pytest
from pytest_codspeed import BenchmarkFixture

from sharedbox import Capacity, SharedBox

NAMES = count()


class Record(SharedBox):
    a: int
    b: float
    s: Annotated[str, Capacity(32)]


@pytest.fixture
def box() -> Iterator[Record]:
    name = f"bench-codspeed-{os.getpid()}-{next(NAMES)}"
    with Record.create(name, 0, 0.0, "hello") as box:
        yield box
    Record.unlink(name)


def test_write_int(benchmark: BenchmarkFixture, box: Record) -> None:
    benchmark(setattr, box, "a", 1)


def test_read_int(benchmark: BenchmarkFixture, box: Record) -> None:
    benchmark(getattr, box, "a")


def test_write_str(benchmark: BenchmarkFixture, box: Record) -> None:
    benchmark(setattr, box, "s", "hello")


def test_read_str(benchmark: BenchmarkFixture, box: Record) -> None:
    benchmark(getattr, box, "s")


def test_update_two_fields(benchmark: BenchmarkFixture, box: Record) -> None:
    benchmark(box.update, a=1, b=1.5)


def test_snapshot(benchmark: BenchmarkFixture, box: Record) -> None:
    benchmark(box.snapshot)


def test_native_set_int(benchmark: BenchmarkFixture, box: Record) -> None:
    benchmark(box._segment.set, [(0, 1)])


def test_native_get_int(benchmark: BenchmarkFixture, box: Record) -> None:
    benchmark(box._segment.get, 0)
