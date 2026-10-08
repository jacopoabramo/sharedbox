import datetime
import decimal
import enum
import gc
import threading
import time
import uuid
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, NamedTuple, TypedDict

import attrs
import msgspec
import numpy as np
import pytest
from crossproc import update_in_child

from sharedbox import BoxClosedError, Capacity, DType, Shape, SharedBox


@dataclass(frozen=True)
class Point:
    x: float
    y: float


class PlainPoint(Point):
    pass


@dataclass(frozen=True)
class FrozenPoint(Point):
    pass


@attrs.frozen
class Mark:
    n: int


class Spot(msgspec.Struct, frozen=True):
    n: int


class Pair(NamedTuple):
    a: int
    b: Annotated[str, Capacity(4)]


class OpenPair(Pair):
    pass


class Color(enum.Enum):
    RED = 1
    BLUE = 2


class Perm(enum.Flag):
    R = 1
    W = 2


@dataclass(frozen=True)
class Stamp:
    when: datetime.datetime
    color: Color
    perm: Perm
    choice: Literal["a", "b"]
    label: Annotated[bytes, Capacity(4)]
    pair: Pair


@dataclass
class Loose:
    x: float


@dataclass(unsafe_hash=True)
class Hashed:
    x: float


@attrs.define
class Note:
    n: int


class LooseSpot(msgspec.Struct):
    n: int


@dataclass(frozen=True)
class FrozenWithList:
    items: Annotated[list[int], Capacity(2)]


@attrs.frozen
class MarkWithList:
    items: Annotated[list[int], Capacity(2)]


class SpotWithList(msgspec.Struct, frozen=True):
    items: Annotated[list[int], Capacity(2)]


class Options(TypedDict):
    speed: float


class Fixed(SharedBox):
    point: Point
    mark: Mark
    spot: Spot
    pair: Pair
    coords: tuple[int, float]
    floats: Annotated[tuple[float, ...], Capacity(4)]
    tags: Annotated[frozenset[int], Capacity(4)]
    color: Color
    when: datetime.datetime
    amount: Annotated[decimal.Decimal, Capacity(16)]
    key: uuid.UUID
    maybe: Point | None
    z: complex
    day: datetime.date
    clock: datetime.time
    span: datetime.timedelta
    either: int | Point
    maybe_pair: tuple[int, float] | None
    stamp: Stamp
    points: Annotated[tuple[Point, ...], Capacity(4)]
    grid: Annotated[frozenset[tuple[int, int]], Capacity(4)]
    mixed: tuple[Color, Perm, Literal["a", "b"], Annotated[bytes, Capacity(4)]]
    frozen_child: FrozenPoint


FIXED: dict[str, Any] = {
    "point": Point(0.5, 1.5),
    "mark": Mark(1),
    "spot": Spot(1),
    "pair": Pair(1, "a"),
    "coords": (1, 0.5),
    "floats": (0.5, 1.5),
    "tags": frozenset({1, 2}),
    "color": Color.RED,
    "when": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    "amount": decimal.Decimal("1.5"),
    "key": uuid.UUID(int=1),
    "maybe": Point(1.0, 2.0),
    "z": complex(0.5, -1),
    "day": datetime.date(2026, 1, 1),
    "clock": datetime.time(9, 30),
    "span": datetime.timedelta(seconds=5),
    "either": Point(0.5, 0.5),
    "maybe_pair": (1, 0.5),
    "stamp": Stamp(
        datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        Color.RED,
        Perm.R,
        "a",
        b"x",
        Pair(1, "a"),
    ),
    "points": (Point(0.0, 1.0),),
    "grid": frozenset({(1, 2)}),
    "mixed": (Color.RED, Perm.R, "a", b"x"),
    "frozen_child": FrozenPoint(0.5, 1.5),
}
OTHER: dict[str, Any] = {
    "point": Point(2.5, 3.5),
    "mark": Mark(2),
    "spot": Spot(2),
    "pair": Pair(2, "b"),
    "coords": (2, 1.5),
    "floats": (2.5,),
    "tags": frozenset({3}),
    "color": Color.BLUE,
    "when": datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
    "amount": decimal.Decimal("2.5"),
    "key": uuid.UUID(int=2),
    "maybe": None,
    "z": complex(1, 1),
    "day": datetime.date(2027, 1, 1),
    "clock": datetime.time(10, 0),
    "span": datetime.timedelta(days=1),
    "either": Point(1.0, 1.0),
    "maybe_pair": None,
    "stamp": Stamp(
        datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
        Color.BLUE,
        Perm.R | Perm.W,
        "b",
        b"y",
        Pair(2, "b"),
    ),
    "points": (Point(1.0, 2.0), Point(3.0, 4.0)),
    "grid": frozenset({(3, 4)}),
    "mixed": (Color.BLUE, Perm.R | Perm.W, "b", b"y"),
    "frozen_child": FrozenPoint(2.5, 3.5),
}


class Changeable(SharedBox):
    numbers: Annotated[list[int], Capacity(4)]
    table: Annotated[dict[int, int], Capacity(4)]
    members: Annotated[set[int], Capacity(4)]
    options: Options
    loose: Loose
    hashed: Hashed
    note: Note
    raw: Annotated[bytearray, Capacity(4)]
    image: Annotated[np.ndarray, Shape(2), DType("int64")]
    frozen_with_list: FrozenWithList
    maybe_list: Annotated[list[int] | None, Capacity(2)]
    open_pair: OpenPair
    loose_spot: LooseSpot
    mark_with_list: MarkWithList
    spot_with_list: SpotWithList
    tuple_with_list: tuple[int, Annotated[list[int], Capacity(2)]]
    loose_points: Annotated[tuple[Loose, ...], Capacity(2)]
    either_loose: int | Loose
    plain_point: PlainPoint


CHANGEABLE: dict[str, Any] = {
    "numbers": [1],
    "table": {1: 1},
    "members": {1},
    "options": Options(speed=1.0),
    "loose": Loose(1.0),
    "hashed": Hashed(1.0),
    "note": Note(1),
    "raw": bytearray(b"a"),
    "image": np.zeros(2, np.int64),
    "frozen_with_list": FrozenWithList([1]),
    "maybe_list": [1],
    "open_pair": OpenPair(1, "a"),
    "loose_spot": LooseSpot(1),
    "mark_with_list": MarkWithList([1]),
    "spot_with_list": SpotWithList([1]),
    "tuple_with_list": (1, [1]),
    "loose_points": (Loose(1.0),),
    "either_loose": Loose(1.0),
    "plain_point": PlainPoint(1.0, 2.0),
}
CHANGES: dict[str, Callable[[Any], object]] = {
    "numbers": lambda v: v.append(2),
    "table": lambda v: v.update({2: 2}),
    "members": lambda v: v.add(2),
    "options": lambda v: v.update(speed=2.0),
    "loose": lambda v: setattr(v, "x", 2.0),
    "hashed": lambda v: setattr(v, "x", 2.0),
    "note": lambda v: setattr(v, "n", 2),
    "raw": lambda v: v.extend(b"b"),
    "image": lambda v: v.fill(7),
    "frozen_with_list": lambda v: v.items.append(2),
    "maybe_list": lambda v: v.append(2),
    "open_pair": lambda v: setattr(v, "extra", 2),
    "loose_spot": lambda v: setattr(v, "n", 2),
    "mark_with_list": lambda v: v.items.append(2),
    "spot_with_list": lambda v: v.items.append(2),
    "tuple_with_list": lambda v: v[1].append(2),
    "loose_points": lambda v: setattr(v[0], "x", 2.0),
    "either_loose": lambda v: setattr(v, "x", 2.0),
    "plain_point": lambda v: setattr(v, "extra", 2),
}


def stored_equal(read: object, stored: Any) -> bool:
    if isinstance(read, np.ndarray):
        return bool(np.array_equal(read, stored))
    return read == stored and not hasattr(read, "extra")


@pytest.mark.parametrize("name", list(CHANGES))
def test_a_changeable_value_is_never_shared(unique_name: str, name: str) -> None:
    """Check that changing a value read from a field whose values can be changed leaves the next read a new object equal to the stored value."""
    with Changeable.create(unique_name, **CHANGEABLE) as box:
        first = getattr(box, name)
        CHANGES[name](first)
        second = getattr(box, name)
        assert second is not first
        assert stored_equal(second, CHANGEABLE[name])
    Changeable.unlink(unique_name)


@pytest.mark.parametrize("name", list(FIXED))
def test_a_value_that_cannot_change_is_reused_until_a_write(
    unique_name: str, name: str
) -> None:
    """Check that two reads of a field whose values cannot change return the same object, and a read after a write returns the written value."""
    with Fixed.create(unique_name, **FIXED) as box:
        first = getattr(box, name)
        assert getattr(box, name) is first
        setattr(box, name, OTHER[name])
        assert getattr(box, name) == OTHER[name]
    Fixed.unlink(unique_name)


def test_a_write_from_another_process_replaces_every_reused_value(
    unique_name: str,
) -> None:
    """Check that reads after another process wrote every reused field return the new values."""
    with Fixed.create(unique_name, **FIXED) as box:
        assert {name: getattr(box, name) for name in FIXED} == FIXED
        update_in_child(box, **OTHER)
        assert {name: getattr(box, name) for name in FIXED} == OTHER
    Fixed.unlink(unique_name)


def test_two_boxes_of_one_class_keep_their_own_values(
    names: Callable[[str], str],
) -> None:
    """Check that two boxes of one class each read their own value of a reused field."""
    with (
        Fixed.create(names("a"), **FIXED) as a,
        Fixed.create(names("b"), **{**FIXED, "point": OTHER["point"]}) as b,
    ):
        assert (a.point, b.point) == (FIXED["point"], OTHER["point"])
        assert (a.point, b.point) == (FIXED["point"], OTHER["point"])
    Fixed.unlink(names("a"))
    Fixed.unlink(names("b"))


@dataclass(frozen=True)
class Owned:
    n: int


class Holder(SharedBox):
    owned: Owned


def test_a_closed_box_refuses_a_read_it_could_answer_from_reuse(
    unique_name: str,
) -> None:
    """Check that reading a reused field after close() raises BoxClosedError."""
    box = Fixed.create(unique_name, **FIXED)
    box.point  # noqa: B018
    box.close()
    with pytest.raises(BoxClosedError):
        box.point  # noqa: B018
    Fixed.unlink(unique_name)


def test_close_lets_go_of_reused_values(unique_name: str) -> None:
    """Check that close() drops the values reads kept for reuse."""
    with Holder.create(unique_name, Owned(1)) as box:
        kept = weakref.ref(box.owned)
        assert kept() is not None
    gc.collect()
    assert kept() is None
    Holder.unlink(unique_name)


def test_a_reused_value_that_refers_to_its_box_is_collected(unique_name: str) -> None:
    """Check that the cycle collector frees a box whose reused value refers back to it."""
    box = Holder.create(unique_name, Owned(1))
    # A frozen dataclass without __slots__ still takes attributes through object.__setattr__.
    object.__setattr__(box.owned, "box", box)
    gone = weakref.ref(box)
    del box
    gc.collect()
    assert gone() is None
    Holder.unlink(unique_name)


def test_close_lets_go_of_a_value_a_last_event_callback_read(unique_name: str) -> None:
    """Check that close() drops a value read by a callback that close() delivers."""
    kept: list[weakref.ref[Owned]] = []
    with Holder.create(unique_name, Owned(1)) as box:
        box.events.owned.connect(lambda new, old: kept.append(weakref.ref(box.owned)))
        box.owned = Owned(2)
    gc.collect()
    assert kept
    assert all(ref() is None for ref in kept)
    Holder.unlink(unique_name)


@dataclass(frozen=True)
class Twin:
    left: int
    right: int


class Twins(SharedBox):
    twin: Twin = Twin(0, 0)


def test_threads_reading_a_reused_field_see_whole_values_in_order(
    unique_name: str,
) -> None:
    """Check that threads reading a reused record while another thread writes it see only written values, never older than one they saw before."""
    stop = threading.Event()
    started = threading.Barrier(4)
    wrong: list[Twin] = []
    errors: list[Exception] = []
    seen: list[int] = []
    with Twins.create(unique_name) as box:

        def write() -> None:
            n = 0
            try:
                started.wait()
                while not stop.is_set():
                    n += 1
                    box.twin = Twin(n, n)
            except Exception as error:
                errors.append(error)

        def read() -> None:
            last = 0
            distinct = 0
            deadline = time.monotonic() + 10
            try:
                started.wait()
                while distinct < 20 and time.monotonic() < deadline:
                    twin = box.twin
                    if twin.left != twin.right or twin.left < last:
                        wrong.append(twin)
                    if twin.left > last:
                        distinct += 1
                    last = twin.left
            except Exception as error:
                errors.append(error)
            seen.append(distinct)

        writer = threading.Thread(target=write)
        readers = [threading.Thread(target=read) for _ in range(3)]
        writer.start()
        for thread in readers:
            thread.start()
        for thread in readers:
            thread.join(15)
        stop.set()
        writer.join(15)
    Twins.unlink(unique_name)
    assert not any(thread.is_alive() for thread in [writer, *readers])
    assert (errors, wrong, seen) == ([], [], [20, 20, 20])


def test_closing_a_box_while_threads_read_a_reused_field_raises_only_box_closed_error(
    unique_name: str,
) -> None:
    """Check that threads reading a reused field while the box closes get values or BoxClosedError and nothing else."""
    errors: list[BaseException] = []
    box = Twins.create(unique_name)
    started = threading.Barrier(4)

    def read() -> None:
        started.wait()
        try:
            while True:
                box.twin  # noqa: B018
        except BoxClosedError:
            pass
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=read) for _ in range(3)]
    for thread in threads:
        thread.start()
    started.wait()
    box.close()
    for thread in threads:
        thread.join(10)
    Twins.unlink(unique_name)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
