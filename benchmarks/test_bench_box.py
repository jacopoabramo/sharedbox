import os
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import count
from typing import Annotated

import msgspec
import pytest
from pytest_codspeed import BenchmarkFixture

from sharedbox import BoundedList, Capacity, SharedBox

NAMES = count()


class Record(SharedBox):
    a: int
    b: float
    s: Annotated[str, Capacity(32)]


class Samples(SharedBox):
    floats: Annotated[list[float], Capacity(16)]
    l1k: Annotated[list[float], Capacity(1024)]
    l64k: Annotated[list[float], Capacity(65536)]
    b16: Annotated[BoundedList[float], Capacity(16)]
    b1k: Annotated[BoundedList[float], Capacity(1024)]
    b64k: Annotated[BoundedList[float], Capacity(65536)]


class Keyed(SharedBox):
    s16: Annotated[set[int], Capacity(16)]
    s1k: Annotated[set[int], Capacity(1024)]
    s64k: Annotated[set[int], Capacity(65536)]
    d16: Annotated[dict[int, float], Capacity(16)]
    d1k: Annotated[dict[int, float], Capacity(1024)]
    d64k: Annotated[dict[int, float], Capacity(65536)]


@dataclass(frozen=True)
class Quad:
    a: int
    b: float
    c: bool
    d: int


class Quads(SharedBox):
    quad: Quad


Tag = Annotated[str, Capacity(8)]


class Collections(SharedBox):
    long: Annotated[list[int], Capacity(64)]
    names: Annotated[tuple[Tag, ...], Capacity(8)]
    tags: Annotated[frozenset[int], Capacity(16)]
    scores: Annotated[dict[Tag, int], Capacity(8)]


class Ticket(msgspec.Struct):
    a: int
    b: float
    c: bool
    d: int


class Tickets(SharedBox):
    ticket: Ticket


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
    with Samples.create(
        name,
        [0.5] * 16,
        [0.5] * 1024,
        [0.5] * 65536,
        BoundedList[float]([0.5] * 16, capacity=16),
        BoundedList[float]([0.5] * 1024, capacity=1024),
        BoundedList[float]([0.5] * 65536, capacity=65536),
    ) as box:
        yield box
    Samples.unlink(name)


LIST_FIELDS = [("floats", 16), ("l1k", 1024), ("l64k", 65536)]


BOUNDED_FIELDS = [("b16", 16), ("b1k", 1024), ("b64k", 65536)]


def walk(values: list[float] | BoundedList[float]) -> None:
    for _ in values:
        pass


@pytest.mark.parametrize(("field", "size"), LIST_FIELDS)
def test_read_float_list(
    benchmark: BenchmarkFixture, samples: Samples, field: str, size: int
) -> None:
    """Time reading a list field."""
    benchmark(getattr, samples, field)


@pytest.mark.parametrize(("field", "size"), LIST_FIELDS)
def test_write_float_list(
    benchmark: BenchmarkFixture, samples: Samples, field: str, size: int
) -> None:
    """Time writing a list field."""
    benchmark(setattr, samples, field, [0.5] * size)


@pytest.mark.parametrize(("field", "size"), LIST_FIELDS)
def test_iterate_float_list(
    benchmark: BenchmarkFixture, samples: Samples, field: str, size: int
) -> None:
    """Time walking the elements of a list field."""
    benchmark(lambda: walk(getattr(samples, field)))


@pytest.mark.parametrize(("field", "size"), LIST_FIELDS)
def test_index_float_list(
    benchmark: BenchmarkFixture, samples: Samples, field: str, size: int
) -> None:
    """Time reading the middle element of a list field."""
    benchmark(lambda: getattr(samples, field)[size // 2])


@pytest.mark.parametrize("field", [f for f, _ in BOUNDED_FIELDS])
def test_read_bounded_list(
    benchmark: BenchmarkFixture, samples: Samples, field: str
) -> None:
    """Time reading a BoundedList field."""
    benchmark(getattr, samples, field)


@pytest.mark.parametrize(("field", "size"), BOUNDED_FIELDS)
def test_write_bounded_list(
    benchmark: BenchmarkFixture, samples: Samples, field: str, size: int
) -> None:
    """Time writing a BoundedList to its field."""
    value = BoundedList[float]([0.5] * size, capacity=size)
    benchmark(setattr, samples, field, value)


@pytest.mark.parametrize("field", [f for f, _ in BOUNDED_FIELDS])
def test_iterate_bounded_list(
    benchmark: BenchmarkFixture, samples: Samples, field: str
) -> None:
    """Time walking the elements of a BoundedList field."""
    benchmark(lambda: walk(getattr(samples, field)))


@pytest.mark.parametrize(("field", "size"), BOUNDED_FIELDS)
def test_index_bounded_list(
    benchmark: BenchmarkFixture, samples: Samples, field: str, size: int
) -> None:
    """Time reading the middle element of a BoundedList field."""
    benchmark(lambda: getattr(samples, field)[size // 2])


@pytest.fixture
def keyed() -> Iterator[Keyed]:
    name = f"bench-codspeed-{os.getpid()}-{next(NAMES)}"
    with Keyed.create(
        name,
        set(range(16)),
        set(range(1024)),
        set(range(65536)),
        dict.fromkeys(range(16), 0.5),
        dict.fromkeys(range(1024), 0.5),
        dict.fromkeys(range(65536), 0.5),
    ) as box:
        yield box
    Keyed.unlink(name)


SET_FIELDS = [("s16", 16), ("s1k", 1024), ("s64k", 65536)]
DICT_FIELDS = [("d16", 16), ("d1k", 1024), ("d64k", 65536)]


def walk_keys(values: set[int] | dict[int, float]) -> None:
    for _ in values:
        pass


@pytest.mark.parametrize("field", [f for f, _ in SET_FIELDS])
def test_read_int_set(benchmark: BenchmarkFixture, keyed: Keyed, field: str) -> None:
    """Time reading a set field."""
    benchmark(getattr, keyed, field)


@pytest.mark.parametrize(("field", "size"), SET_FIELDS)
def test_write_int_set(
    benchmark: BenchmarkFixture, keyed: Keyed, field: str, size: int
) -> None:
    """Time writing a set field."""
    benchmark(setattr, keyed, field, set(range(size)))


@pytest.mark.parametrize(("field", "size"), SET_FIELDS)
def test_contains_int_set(
    benchmark: BenchmarkFixture, keyed: Keyed, field: str, size: int
) -> None:
    """Time a membership test of the middle key in a set field."""
    benchmark(lambda: size // 2 in getattr(keyed, field))


@pytest.mark.parametrize("field", [f for f, _ in SET_FIELDS])
def test_iterate_int_set(benchmark: BenchmarkFixture, keyed: Keyed, field: str) -> None:
    """Time walking the elements of a set field."""
    benchmark(lambda: walk_keys(getattr(keyed, field)))


@pytest.mark.parametrize("field", [f for f, _ in DICT_FIELDS])
def test_read_int_dict(benchmark: BenchmarkFixture, keyed: Keyed, field: str) -> None:
    """Time reading a dict field."""
    benchmark(getattr, keyed, field)


@pytest.mark.parametrize(("field", "size"), DICT_FIELDS)
def test_write_int_dict(
    benchmark: BenchmarkFixture, keyed: Keyed, field: str, size: int
) -> None:
    """Time writing a dict field."""
    benchmark(setattr, keyed, field, dict.fromkeys(range(size), 0.5))


@pytest.mark.parametrize(("field", "size"), DICT_FIELDS)
def test_contains_int_dict(
    benchmark: BenchmarkFixture, keyed: Keyed, field: str, size: int
) -> None:
    """Time a membership test of the middle key in a dict field."""
    benchmark(lambda: size // 2 in getattr(keyed, field))


@pytest.mark.parametrize("field", [f for f, _ in DICT_FIELDS])
def test_iterate_int_dict(
    benchmark: BenchmarkFixture, keyed: Keyed, field: str
) -> None:
    """Time walking the elements of a dict field."""
    benchmark(lambda: walk_keys(getattr(keyed, field)))


@pytest.fixture
def quads() -> Iterator[Quads]:
    name = f"bench-codspeed-{os.getpid()}-{next(NAMES)}"
    with Quads.create(name, Quad(1, 1.5, True, 2)) as box:
        yield box
    Quads.unlink(name)


def test_read_record(benchmark: BenchmarkFixture, quads: Quads) -> None:
    benchmark(getattr, quads, "quad")


@pytest.fixture
def bag() -> Iterator[Collections]:
    name = f"bench-codspeed-{os.getpid()}-{next(NAMES)}"
    with Collections.create(
        name,
        list(range(64)),
        ("a",) * 8,
        frozenset(range(16)),
        {f"k{i}": i for i in range(8)},
    ) as box:
        yield box
    Collections.unlink(name)


def test_read_long_list(benchmark: BenchmarkFixture, bag: Collections) -> None:
    """Time reading a list of 64 ints."""
    benchmark(getattr, bag, "long")


def test_write_read_str_tuple(benchmark: BenchmarkFixture, bag: Collections) -> None:
    """Time writing and reading a tuple of 8 strings."""
    names = ("a",) * 8

    def write_read() -> object:
        bag.names = names
        return bag.names

    benchmark(write_read)


def test_write_read_frozenset(benchmark: BenchmarkFixture, bag: Collections) -> None:
    """Time writing and reading a frozenset of 16 ints."""
    tags = frozenset(range(16))

    def write_read() -> object:
        bag.tags = tags
        return bag.tags

    benchmark(write_read)


def test_read_dict(benchmark: BenchmarkFixture, bag: Collections) -> None:
    """Time reading a dict of 8 string keys."""
    benchmark(getattr, bag, "scores")


@pytest.fixture
def tickets() -> Iterator[Tickets]:
    name = f"bench-codspeed-{os.getpid()}-{next(NAMES)}"
    with Tickets.create(name, Ticket(1, 1.5, True, 2)) as box:
        yield box
    Tickets.unlink(name)


def test_read_msgspec_record(benchmark: BenchmarkFixture, tickets: Tickets) -> None:
    """Time reading a msgspec Struct field."""
    benchmark(getattr, tickets, "ticket")
