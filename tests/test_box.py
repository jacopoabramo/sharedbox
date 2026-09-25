import contextlib
import gc
import multiprocessing as mp
import pickle
import threading
import time
import types
from collections.abc import Iterator
from dataclasses import KW_ONLY
from typing import Annotated, cast

import pytest

from sharedbox import (
    BoxClosedError,
    Capacity,
    SchemaMismatchError,
    SegmentExistsError,
    SegmentNotFoundError,
    SharedBox,
)


class Point(SharedBox):
    x: float = 0.0
    y: float = 0.0
    label: Annotated[str, Capacity(16)] = ""


class Pair(SharedBox):
    a: int = 0
    b: int = 0


class Required(SharedBox):
    value: int


class Motor(SharedBox):
    position: int
    enabled: bool
    label: Annotated[str, Capacity(32)]


def move_in_child(results: "mp.Queue[dict[str, object]]") -> None:
    motor = Motor.attach()
    results.put(motor.snapshot())
    motor.position = 10
    motor.close()


class Config(SharedBox, kw_only=True):
    rate: float
    enabled: bool = False
    retries: int


class Mixed(SharedBox):
    a: int
    _: KW_ONLY
    b: int = 0
    c: int


@pytest.fixture(autouse=True)
def free_motor_name() -> Iterator[None]:
    yield
    with contextlib.suppress(SegmentNotFoundError):
        Motor.unlink()


def with_x(namespace: dict[str, object]) -> None:
    namespace["__annotations__"] = {"x": int}
    namespace["x"] = 0


def read_in_child(box: Point, results: "mp.Queue[dict[str, object]]") -> None:
    results.put(box.snapshot())
    box.x = 9.0
    box.close()


def write_pairs(name: str, count: int) -> None:
    box = Pair.attach(name)
    for i in range(count):
        box.update(a=i, b=i)
    box.close()


def test_create_attach_and_share(unique_name: str) -> None:
    with (
        Point.create(unique_name, x=1.5, label="start") as owner,
        Point.attach(unique_name) as other,
    ):
        assert (other.x, other.y, other.label) == (1.5, 0.0, "start")
        other.y = 2.5
        assert owner.y == 2.5
        assert owner.snapshot() == {"x": 1.5, "y": 2.5, "label": "start"}


def test_positional_values_and_attach_by_class() -> None:
    ctx = mp.get_context("spawn")
    results: mp.Queue[dict[str, object]] = ctx.Queue()
    with Motor(1, False, "hello") as motor:
        child = ctx.Process(target=move_in_child, args=(results,))
        child.start()
        assert results.get(timeout=20) == {
            "position": 1,
            "enabled": False,
            "label": "hello",
        }
        child.join()
        assert child.exitcode == 0
        assert motor.position == 10


def test_second_box_of_a_class_needs_a_name() -> None:
    with Motor(1, False, "a"), pytest.raises(SegmentExistsError):
        Motor(2, True, "b")


def test_default_name_ignores_the_spawned_main_module() -> None:
    namespace = {"__annotations__": {"x": int}, "__qualname__": "Spawned"}
    parent = cast(
        type[SharedBox],
        type("Spawned", (SharedBox,), {**namespace, "__module__": "__main__"}),
    )
    child = cast(
        type[SharedBox],
        type("Spawned", (SharedBox,), {**namespace, "__module__": "__mp_main__"}),
    )
    try:
        with parent(4), child.attach() as other:
            assert other.x == 4  # type: ignore[attr-defined] # x is a field added dynamically, above
    finally:
        parent.unlink()


def test_class_keyword_sets_the_name(unique_name: str) -> None:
    named = cast(
        type[SharedBox],
        types.new_class("Named", (SharedBox,), {"name": unique_name}, with_x),
    )
    with named() as box, named.attach() as other:
        assert box.name == other.name == unique_name


def test_positional_and_keyword_values(unique_name: str) -> None:
    with pytest.raises(TypeError, match="3"):
        Pair.create(unique_name, 1, 2, 3)
    with pytest.raises(TypeError, match="a"):
        Pair.create(unique_name, 1, a=2)
    with Pair.create(unique_name, 1, b=2) as box:
        assert box.snapshot() == {"a": 1, "b": 2}


def test_keyword_only_class(unique_name: str) -> None:
    with pytest.raises(TypeError, match="0"):
        Config.create(unique_name, 1.0)
    with Config.create(unique_name, rate=1.0, retries=3) as box:
        assert box.snapshot() == {"rate": 1.0, "enabled": False, "retries": 3}


def test_keyword_only_marker(unique_name: str) -> None:
    with pytest.raises(TypeError, match="1"):
        Mixed.create(unique_name, 1, 2)
    with Mixed.create(unique_name, 1, c=2) as box:
        assert box.snapshot() == {"a": 1, "b": 0, "c": 2}


def test_class_unlink_frees_the_default_name() -> None:
    box = Motor(1, False, "a")
    box.close()
    Motor.unlink()
    with Motor(2, True, "b") as again:
        assert again.position == 2


def test_instance_unlink_uses_the_box_name(unique_name: str) -> None:
    box = Pair.create(unique_name, 1, 2)
    box.unlink()
    box.a = 5
    assert box.a == 5
    box.close()
    with Pair.create(unique_name) as again:
        assert again.a == 0


def test_required_field_after_default() -> None:
    with pytest.raises(TypeError, match="b"):

        class Bad(SharedBox):
            a: int = 0
            b: int  # type: ignore[misc] # the point of this test is that SharedBox rejects this at runtime too


def test_box_passed_to_child_process_arrives_attached(unique_name: str) -> None:
    ctx = mp.get_context("spawn")
    results: mp.Queue[dict[str, object]] = ctx.Queue()
    with Point.create(unique_name, 1.0, label="hi") as box:
        child = ctx.Process(target=read_in_child, args=(box, results))
        child.start()
        assert results.get(timeout=20) == {"x": 1.0, "y": 0.0, "label": "hi"}
        child.join()
        assert child.exitcode == 0
        assert box.x == 9.0


def test_pickle_round_trip_attaches(unique_name: str) -> None:
    with (
        Point.create(unique_name, y=3.0) as box,
        pickle.loads(pickle.dumps(box)) as copy,
    ):
        assert copy.name == unique_name
        assert copy.y == 3.0


def test_update_is_atomic_across_processes(unique_name: str) -> None:
    with Pair.create(unique_name) as box:
        writer = mp.get_context("spawn").Process(
            target=write_pairs, args=(unique_name, 50_000)
        )
        writer.start()
        while writer.is_alive():
            snap = box.snapshot()
            assert snap["a"] == snap["b"]
        writer.join()
        assert writer.exitcode == 0


def test_required_field_must_be_given(unique_name: str) -> None:
    with pytest.raises(TypeError, match="value"):
        Required.create(unique_name)
    with Required.create(unique_name, value=3) as box:
        assert box.value == 3


def test_unknown_field_is_rejected(unique_name: str) -> None:
    with pytest.raises(TypeError, match="z"):
        Point.create(unique_name, z=1.0)
    with Point.create(unique_name) as box, pytest.raises(TypeError, match="z"):
        box.update(z=1.0)


def test_invalid_value_leaves_field_unchanged(unique_name: str) -> None:
    with Point.create(unique_name, label="ok") as box:
        with pytest.raises(ValueError):
            box.label = "é" * 16
        with pytest.raises(ValueError):
            box.update(x=1.0, label="é" * 16)
        assert (box.x, box.label) == (0.0, "ok")


def test_same_name_twice(unique_name: str) -> None:
    with Point.create(unique_name), pytest.raises(SegmentExistsError):
        Point.create(unique_name)


def shape_class(annotations: dict[str, type]) -> type[SharedBox]:
    return type(
        "Shape", (SharedBox,), {"__qualname__": "Shape", "__annotations__": annotations}
    )


def test_attach_with_changed_class(unique_name: str) -> None:
    with shape_class({"x": float}).create(unique_name, x=1.0):
        with shape_class({"x": float}).attach(unique_name) as same:
            assert same.snapshot() == {"x": 1.0}
        with pytest.raises(SchemaMismatchError):
            shape_class({"x": float, "y": float}).attach(unique_name)
        with pytest.raises(SchemaMismatchError):
            shape_class({"x": int}).attach(unique_name)


@pytest.mark.parametrize("field", ["name", "close", "update", "snapshot", "watch"])
def test_field_named_like_a_method(field: str) -> None:
    with pytest.raises(TypeError, match=field):
        type("Clash", (SharedBox,), {"__annotations__": {field: int}})


def test_bad_default_fails_at_class_definition() -> None:
    with pytest.raises(OverflowError):

        class Bad(SharedBox):
            n: int = 2**64


def test_invalid_explicit_name() -> None:
    with pytest.raises(ValueError):
        Point.create("has space")
    with pytest.raises(ValueError):
        types.new_class("BadName", (SharedBox,), {"name": "has space"}, with_x)
    with pytest.raises(ValueError):
        Point.attach("has space")
    with pytest.raises(ValueError):
        Point.unlink("has space")


@pytest.mark.parametrize(
    "value", [float("inf"), float("-inf"), float("nan"), 0, -1, 86401]
)
def test_bad_lock_timeout_fails_at_class_definition(value: float) -> None:
    with pytest.raises(ValueError):
        types.new_class("BadTimeout", (SharedBox,), {"lock_timeout": value}, with_x)


def test_max_lock_timeout_is_accepted(unique_name: str) -> None:
    named = cast(
        type[SharedBox],
        types.new_class("MaxTimeout", (SharedBox,), {"lock_timeout": 86400}, with_x),
    )
    with named.create(unique_name) as box:
        assert box.x == 0  # type: ignore[attr-defined] # x is a field added dynamically, above


def test_closed_box(unique_name: str) -> None:
    box = Point.create(unique_name)
    box.close()
    box.close()
    assert box.closed
    assert "closed" in repr(box)
    with pytest.raises(BoxClosedError):
        _ = box.x


def test_repr_shows_values(unique_name: str) -> None:
    with Point.create(unique_name, x=1.0) as box:
        assert repr(box) == "Point(x=1.0, y=0.0, label='')"


def test_subclass_inherits_fields(unique_name: str) -> None:
    class Point3(Point):
        z: float = 0.0

    with Point3.create(unique_name, z=4.0) as box:
        assert box.snapshot() == {"x": 0.0, "y": 0.0, "label": "", "z": 4.0}


class Tagged(SharedBox):
    tag: Annotated[str, Capacity(16)] = "sixteen chars ok"


def test_narrowed_default_fails_at_class_definition() -> None:
    with pytest.raises(ValueError):

        class Narrow(Tagged):
            tag: Annotated[str, Capacity(4)]


def test_misspelled_field_raises(unique_name: str) -> None:
    with Point.create(unique_name) as box, pytest.raises(AttributeError):
        box.postion = 3.0  # type: ignore[attr-defined]


def test_collecting_a_box_while_holding_its_watcher_lock_does_not_hang(
    unique_name: str,
) -> None:
    finished = threading.Event()

    def collect_under_the_lock() -> None:
        box = Point.create(unique_name)
        box.events.x.connect(lambda new: None)
        # The watcher thread takes this lock on every pass, so the finalizer runs on
        # a thread the watcher thread is waiting for.
        lock = box._watcher._lock
        with lock:
            time.sleep(0.3)
            del box
            gc.collect()
        finished.set()

    threading.Thread(target=collect_under_the_lock, daemon=True).start()
    assert finished.wait(5)


def test_box_with_the_most_fields(unique_name: str) -> None:
    widest = shape_class({f"f{i}": int for i in range(256)})
    with (
        widest.create(unique_name, *range(256)) as box,
        widest.attach(unique_name) as other,
    ):
        box.update(f0=-1, f255=-2)
        assert list(other.snapshot().values()) == [-1, *range(1, 255), -2]


def test_field_of_the_largest_capacity(unique_name: str) -> None:
    largest = shape_class({"data": Annotated[bytes, Capacity(1 << 20)]})  # type: ignore[dict-item] # Annotated is a valid field annotation
    value = bytes(range(256)) * 4096
    with (
        largest.create(unique_name, data=value),
        largest.attach(unique_name) as other,
    ):
        assert other.data == value  # type: ignore[attr-defined] # data is a field added dynamically, above
