import os
from collections.abc import Iterator
from dataclasses import dataclass
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


class Samples(SharedBox):
    floats: Annotated[list[float], Capacity(16)]


@dataclass(frozen=True)
class Quad:
    a: int
    b: float
    c: bool
    d: int


class Quads(SharedBox):
    quad: Quad


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


@pytest.fixture
def samples() -> Iterator[Samples]:
    name = f"bench-codspeed-{os.getpid()}-{next(NAMES)}"
    with Samples.create(name, [0.5] * 16) as box:
        yield box
    Samples.unlink(name)


def test_read_float_list(benchmark: BenchmarkFixture, samples: Samples) -> None:
    benchmark(getattr, samples, "floats")


@pytest.fixture
def quads() -> Iterator[Quads]:
    name = f"bench-codspeed-{os.getpid()}-{next(NAMES)}"
    with Quads.create(name, Quad(1, 1.5, True, 2)) as box:
        yield box
    Quads.unlink(name)


def test_read_record(benchmark: BenchmarkFixture, quads: Quads) -> None:
    benchmark(getattr, quads, "quad")
