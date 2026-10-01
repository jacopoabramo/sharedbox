import contextlib
import copy
import gc
import multiprocessing as mp
import os
import pickle
import re
import subprocess
import sys
import threading
import time
import traceback
import types
from collections.abc import Iterator
from dataclasses import KW_ONLY
from multiprocessing.synchronize import Event
from typing import Annotated, Any, cast

import pytest

from sharedbox import (
    BoxClosedError,
    Capacity,
    LockTimeoutError,
    SchemaMismatchError,
    SegmentExistsError,
    SegmentNotFoundError,
    SharedBox,
)
from sharedbox._box import unpickle_box


class Point(SharedBox):
    x: float = 0.0
    y: float = 0.0
    label: Annotated[str, Capacity(16)] = ""


class Pair(SharedBox):
    a: int = 0
    b: int = 0


class Required(SharedBox):
    value: int


# A fixed name, not the class's derived default, so parallel pytest runs (parallel
# tox environments or xdist workers, for instance) never fight over the same segment;
# move_in_child sees the same name because it inherits both variables from this process.
class Motor(
    SharedBox,
    name=f"sbtest-motor-{os.environ['SHAREDBOX_TEST_RUN']}"
    f"{os.environ.get('PYTEST_XDIST_WORKER', '')}",
):
    position: int
    enabled: bool
    label: Annotated[str, Capacity(32)]


def move_in_child(results: "mp.Queue[dict[str, object]]") -> None:
    motor = Motor.attach()
    results.put(motor.snapshot())
    motor.position = 10
    motor.close()


def create_point_and_exit(name: str, created: Event, attached: Event) -> None:
    box = Point.create(name)
    created.set()
    attached.wait(20)
    box.close()


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
    """Check that a box created by name and one attached to it read and write the same values."""
    with (
        Point.create(unique_name, x=1.5, label="start") as owner,
        Point.attach(unique_name) as other,
    ):
        assert (other.x, other.y, other.label) == (1.5, 0.0, "start")
        other.y = 2.5
        assert owner.y == 2.5
        assert owner.snapshot() == {"x": 1.5, "y": 2.5, "label": "start"}


def test_positional_values_and_attach_by_class() -> None:
    """Check that a child process finds a box by its class and that its write reaches the parent."""
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
    """Check that a second box of a class with the default name raises SegmentExistsError."""
    with Motor(1, False, "a"), pytest.raises(SegmentExistsError):
        Motor(2, True, "b")


def test_default_name_ignores_the_spawned_main_module() -> None:
    """Check that a class defined in __main__ and its twin in __mp_main__ get the same default name."""
    # __qualname__ carries the run token and the xdist worker so the derived default
    # name does not collide with another run's or worker's; neither class here is
    # pickled or sent to a child.
    qualname = (
        f"Spawned_{os.environ['SHAREDBOX_TEST_RUN']}"
        f"{os.environ.get('PYTEST_XDIST_WORKER', '')}"
    )
    namespace = {"__annotations__": {"x": int}, "__qualname__": qualname}
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
    """Check that the name class keyword sets the segment name for create and attach."""
    named = cast(
        type[SharedBox],
        types.new_class("Named", (SharedBox,), {"name": unique_name}, with_x),
    )
    with named() as box, named.attach() as other:
        assert box.name == other.name == unique_name


def test_positional_and_keyword_values(unique_name: str) -> None:
    """Check that create rejects too many positional values and a field given twice, and accepts a mix."""
    with pytest.raises(TypeError, match="3"):
        Pair.create(unique_name, 1, 2, 3)
    with pytest.raises(TypeError, match="a"):
        Pair.create(unique_name, 1, a=2)
    with Pair.create(unique_name, 1, b=2) as box:
        assert box.snapshot() == {"a": 1, "b": 2}


def test_keyword_only_class(unique_name: str) -> None:
    """Check that a keyword-only class rejects positional values and accepts the fields by keyword."""
    with pytest.raises(TypeError, match="0"):
        Config.create(unique_name, 1.0)
    with Config.create(unique_name, rate=1.0, retries=3) as box:
        assert box.snapshot() == {"rate": 1.0, "enabled": False, "retries": 3}


def test_keyword_only_marker(unique_name: str) -> None:
    """Check that fields after the keyword-only marker cannot be given positionally."""
    with pytest.raises(TypeError, match="1"):
        Mixed.create(unique_name, 1, 2)
    with Mixed.create(unique_name, 1, c=2) as box:
        assert box.snapshot() == {"a": 1, "b": 0, "c": 2}


def test_class_unlink_frees_the_default_name() -> None:
    """Check that unlinking by class lets a new box take the default name again."""
    box = Motor(1, False, "a")
    box.close()
    Motor.unlink()
    with Motor(2, True, "b") as again:
        assert again.position == 2


def test_instance_unlink_uses_the_box_name(unique_name: str) -> None:
    """Check that unlinking an instance removes its name while the open handle keeps working."""
    box = Pair.create(unique_name, 1, 2)
    box.unlink()
    box.a = 5
    assert box.a == 5
    box.close()
    with Pair.create(unique_name) as again:
        assert again.a == 0


def test_required_field_after_default() -> None:
    """Check that a field without a default after one with a default raises TypeError."""
    with pytest.raises(TypeError, match="b"):

        class Bad(SharedBox):
            a: int = 0
            b: int  # type: ignore[misc] # the point of this test is that SharedBox rejects this at runtime too


def test_box_passed_to_child_process_arrives_attached(unique_name: str) -> None:
    """Check that a box sent to a child process arrives attached to the same segment."""
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
    """Check that unpickling a box attaches to the same segment by name."""
    with (
        Point.create(unique_name, y=3.0) as box,
        pickle.loads(pickle.dumps(box)) as copy,
    ):
        assert copy.name == unique_name
        assert copy.y == 3.0


def test_pickle_carries_the_schema(
    unique_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check that unpickling into a class with a different layout raises SchemaMismatchError."""
    with Point.create(unique_name) as box:
        data = pickle.dumps(box)
        other = type(
            "Point",
            (SharedBox,),
            {
                "__qualname__": "Point",
                "__module__": Point.__module__,
                "__annotations__": {"x": int},
            },
        )
        monkeypatch.setattr(sys.modules[Point.__module__], "Point", other)
        with pytest.raises(SchemaMismatchError, match="Point was pickled by a process"):
            pickle.loads(data)


def test_a_pickle_of_a_box_made_again_is_refused(unique_name: str) -> None:
    """Check that a pickle taken before the segment was recreated raises SchemaMismatchError."""
    box = Point.create(unique_name)
    data = pickle.dumps(box)
    box.close()
    Point.unlink(unique_name)
    with (
        Point.create(unique_name),
        pytest.raises(SchemaMismatchError, match="pickled from a different box"),
    ):
        pickle.loads(data)


def test_unpickle_box_with_three_arguments_still_attaches(unique_name: str) -> None:
    """Check that unpickle_box called with the older three arguments still attaches."""
    with (
        Point.create(unique_name, y=3.0) as box,
        unpickle_box(Point, unique_name, type(box).__layout__.schema_hash) as copy,
    ):
        assert copy.name == unique_name
        assert copy.y == 3.0


def test_pickling_a_closed_box_raises(unique_name: str) -> None:
    """Check that pickling a closed box raises BoxClosedError."""
    box = Point.create(unique_name)
    box.close()
    with pytest.raises(BoxClosedError):
        pickle.dumps(box)


def test_copy_attaches_a_second_handle(unique_name: str) -> None:
    """Check that copy.copy gives a second handle to the same segment that closes independently."""
    with Point.create(unique_name) as box:
        other = copy.copy(box)
        assert other is not box
        other.x = 2.0
        assert box.x == 2.0
        other.close()
        assert not box.closed


def test_update_is_atomic_across_processes(unique_name: str) -> None:
    """Check that a reader never sees a half-applied update from a writer in another process."""
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
    """Check that create without a required field raises TypeError."""
    with pytest.raises(TypeError, match="value"):
        Required.create(unique_name)
    with Required.create(unique_name, value=3) as box:
        assert box.value == 3


def test_unknown_field_is_rejected(unique_name: str) -> None:
    """Check that create and update reject a field name the class does not declare."""
    with pytest.raises(TypeError, match="z"):
        Point.create(unique_name, z=1.0)
    with Point.create(unique_name) as box, pytest.raises(TypeError, match="z"):
        box.update(z=1.0)


def test_invalid_value_leaves_field_unchanged(unique_name: str) -> None:
    """Check that a rejected value from a write or an update leaves every field unchanged."""
    with Point.create(unique_name, label="ok") as box:
        with pytest.raises(ValueError):
            box.label = "é" * 16
        with pytest.raises(ValueError):
            box.update(x=1.0, label="é" * 16)
        assert (box.x, box.label) == (0.0, "ok")


def test_same_name_twice(unique_name: str) -> None:
    """Check that creating a second box under a taken name raises SegmentExistsError."""
    with Point.create(unique_name), pytest.raises(SegmentExistsError):
        Point.create(unique_name)


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows frees the name with its last handle"
)
def test_exists_names_the_class_to_unlink(unique_name: str) -> None:
    """Check that the SegmentExistsError message names the unlink call that frees the name."""
    context = mp.get_context("spawn")
    created, attached = context.Event(), context.Event()
    child = context.Process(
        target=create_point_and_exit, args=(unique_name, created, attached)
    )
    child.start()
    assert created.wait(20)
    attached.set()
    child.join(20)
    assert child.exitcode == 0
    with pytest.raises(
        SegmentExistsError, match=re.escape(f"Point.unlink('{unique_name}')")
    ):
        Point.create(unique_name)


def shape_class(annotations: dict[str, type]) -> type[SharedBox]:
    return type(
        "Shape", (SharedBox,), {"__qualname__": "Shape", "__annotations__": annotations}
    )


def test_attach_with_changed_class(unique_name: str) -> None:
    """Check that attach accepts an identical layout and raises SchemaMismatchError for an added or retyped field."""
    with shape_class({"x": float}).create(unique_name, x=1.0):
        with shape_class({"x": float}).attach(unique_name) as same:
            assert same.snapshot() == {"x": 1.0}
        with pytest.raises(SchemaMismatchError):
            shape_class({"x": float, "y": float}).attach(unique_name)
        with pytest.raises(SchemaMismatchError):
            shape_class({"x": int}).attach(unique_name)


@pytest.mark.parametrize("field", ["name", "close", "update", "snapshot", "watch"])
def test_field_named_like_a_method(field: str) -> None:
    """Check that a field named like a box method raises TypeError at class definition."""
    with pytest.raises(TypeError, match=field):
        type("Clash", (SharedBox,), {"__annotations__": {field: int}})


def test_bad_default_fails_at_class_definition() -> None:
    """Check that an out-of-range default raises OverflowError at class definition."""
    with pytest.raises(OverflowError):

        class Bad(SharedBox):
            n: int = 2**64


def test_invalid_explicit_name() -> None:
    """Check that an invalid box name raises ValueError in create, attach, unlink and the class keyword."""
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
    """Check that a non-finite or out-of-range lock_timeout raises ValueError at class definition."""
    with pytest.raises(ValueError):
        types.new_class("BadTimeout", (SharedBox,), {"lock_timeout": value}, with_x)


def test_max_lock_timeout_is_accepted(unique_name: str) -> None:
    """Check that a lock_timeout of 86400 seconds is accepted."""
    named = cast(
        type[SharedBox],
        types.new_class("MaxTimeout", (SharedBox,), {"lock_timeout": 86400}, with_x),
    )
    with named.create(unique_name) as box:
        assert box.x == 0  # type: ignore[attr-defined] # x is a field added dynamically, above


def test_closed_box(unique_name: str) -> None:
    """Check that close is idempotent, repr says closed and a read raises BoxClosedError."""
    box = Point.create(unique_name)
    box.close()
    box.close()
    assert box.closed
    assert "closed" in repr(box)
    with pytest.raises(BoxClosedError):
        _ = box.x


def test_repr_shows_values(unique_name: str) -> None:
    """Check that repr lists the class name and the current field values."""
    with Point.create(unique_name, x=1.0) as box:
        assert repr(box) == "Point(x=1.0, y=0.0, label='')"


def test_subclass_inherits_fields(unique_name: str) -> None:
    """Check that a subclass keeps its parent's fields and adds its own."""

    class Point3(Point):
        z: float = 0.0

    with Point3.create(unique_name, z=4.0) as box:
        assert box.snapshot() == {"x": 0.0, "y": 0.0, "label": "", "z": 4.0}


class Tagged(SharedBox):
    tag: Annotated[str, Capacity(16)] = "sixteen chars ok"


def test_narrowed_default_fails_at_class_definition() -> None:
    """Check that redeclaring a field with a smaller capacity than its inherited default raises ValueError."""
    with pytest.raises(ValueError):

        class Narrow(Tagged):
            tag: Annotated[str, Capacity(4)]


def test_misspelled_field_raises(unique_name: str) -> None:
    """Check that assigning to a name that is not a field raises AttributeError."""
    with Point.create(unique_name) as box, pytest.raises(AttributeError):
        box.postion = 3.0  # type: ignore[attr-defined]


def test_collecting_a_box_while_holding_its_watcher_lock_does_not_hang(
    unique_name: str,
) -> None:
    """Check that garbage collection of a box does not hang while the watcher thread's lock is held."""
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
    """Check that a box with 256 fields creates, attaches and updates correctly."""
    widest = shape_class({f"f{i}": int for i in range(256)})
    with (
        widest.create(unique_name, *range(256)) as box,
        widest.attach(unique_name) as other,
    ):
        box.update(f0=-1, f255=-2)
        assert list(other.snapshot().values()) == [-1, *range(1, 255), -2]


def test_field_of_the_largest_capacity(unique_name: str) -> None:
    """Check that a bytes field of the largest capacity round-trips its full value."""
    largest = shape_class({"data": Annotated[bytes, Capacity(1 << 20)]})  # type: ignore[dict-item] # Annotated is a valid field annotation
    value = bytes(range(256)) * 4096
    with (
        largest.create(unique_name, data=value),
        largest.attach(unique_name) as other,
    ):
        assert other.data == value  # type: ignore[attr-defined] # data is a field added dynamically, above


class Chunk(SharedBox):
    data: Annotated[bytes, Capacity(4096)] = b""


class Quick(SharedBox, lock_timeout=0.2):
    value: int = 0


@pytest.mark.parametrize("size", [0, 255, 256, 257, 4096])
def test_values_either_side_of_the_read_buffer(unique_name: str, size: int) -> None:
    """Check that values around the read buffer size round-trip and count as one write."""
    value = (bytes(range(256)) * 16)[:size]
    with Chunk.create(unique_name) as box, Chunk.attach(unique_name) as other:
        box.data = value
        assert other.data == value
        assert other._segment.get_versioned(0) == (1, value)
        assert other._segment._read(0) == value


def test_field_errors_match_the_segment(unique_name: str) -> None:
    """Check that field assignment, deletion and closed access raise the documented errors and messages."""
    box = Point.create(unique_name)
    with pytest.raises(TypeError, match=r"Point\.x expects float, got str"):
        box.x = "1"  # type: ignore[assignment]
    with pytest.raises(AttributeError, match="^a SharedBox field cannot be deleted$"):
        del box.x
    assert box.x == 0.0
    box.close()
    with pytest.raises(BoxClosedError):
        box.x = 1.0


def test_a_held_lock_times_out_field_reads_and_writes(unique_name: str) -> None:
    """Check that a held write lock makes reads and writes raise LockTimeoutError until it is released."""
    with Quick.create(unique_name) as box, Quick.attach(unique_name) as other:
        box._segment._hold_write_lock()
        with pytest.raises(LockTimeoutError, match=rf"locked by pid {os.getpid()}\b"):
            _ = other.value
        with pytest.raises(LockTimeoutError):
            other.value = 1
        box._segment._release_held_lock()
        other.value = 2
        assert box.value == 2


def test_a_box_without_a_segment_raises_attribute_error() -> None:
    """Check that a box built without a segment raises AttributeError on field access."""
    box = Point.__new__(Point)
    with pytest.raises(AttributeError, match="_segment"):
        _ = box.x
    with pytest.raises(AttributeError, match="_segment"):
        box.x = 1.0


def test_a_subclass_with_a_dict_still_writes_to_the_box(unique_name: str) -> None:
    """Check that a subclass with a __dict__ still stores its fields in the segment."""

    class Loose(Point):
        __slots__ = ("__dict__",)

    with Loose.create(unique_name) as box, Loose.attach(unique_name) as other:
        box.note = "local"  # type: ignore[attr-defined] # Loose has a __dict__
        box.x = 2.0
        assert (other.x, box.note) == (2.0, "local")  # type: ignore[attr-defined] # as above


def test_a_segment_slot_holding_something_else_raises_type_error() -> None:
    """Check that a segment slot holding a non-Segment value raises TypeError on field access."""
    box = Point.__new__(Point)
    box._segment = 3  # type: ignore[assignment]
    with pytest.raises(TypeError, match="^_segment does not hold a Segment$"):
        _ = box.x
    with pytest.raises(TypeError, match="^_segment does not hold a Segment$"):
        box.x = 1.0


UNINITIALISED = """
from sharedbox import SharedBox
from sharedbox._native import Field, Segment

class Point(SharedBox):
    x: float = 0.0

class Plain:
    x = Field.__new__(Field)

box = Point.__new__(Point)
box._segment = Segment.__new__(Segment)
for action in (lambda: Plain().x, lambda: setattr(Plain(), "x", 1.0), lambda: box.x, lambda: setattr(box, "x", 1.0)):
    try:
        action()
    except TypeError as e:
        print(e)
"""


def test_uninitialised_native_objects_raise_type_error() -> None:
    """Check that uninitialised native objects raise TypeError instead of crashing the interpreter."""
    # In a child process, because reaching an uninitialised object would crash the interpreter.
    done = subprocess.run(
        [sys.executable, "-c", UNINITIALISED],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert (done.returncode, done.stdout.splitlines()) == (
        0,
        ["Field.__init__ was not called"] * 2
        + ["_segment does not hold a Segment"] * 2,
    )


def test_a_subclass_cannot_declare_its_own_segment_slot() -> None:
    """Check that a subclass declaring a _segment slot raises TypeError."""
    with pytest.raises(TypeError, match="_segment"):

        class Shadow(Point):
            __slots__ = ("_segment",)


RUN = f"{os.environ['SHAREDBOX_TEST_RUN']}{os.environ.get('PYTEST_XDIST_WORKER', '')}"


def reading_class(module: str, identity: str) -> type[SharedBox]:
    def body(namespace: dict[str, object]) -> None:
        namespace["__annotations__"] = {"value": int}
        namespace["__module__"] = module

    return cast(
        type[SharedBox],
        types.new_class("Reading", (SharedBox,), {"identity": identity}, body),
    )


def test_default_name_and_hash_follow_the_published_vector() -> None:
    """Check that the default name and schema hash of a known class match the published values."""
    namespace = {
        "__annotations__": {
            "position": int,
            "enabled": bool,
            "label": Annotated[str, Capacity(32)],
        },
        "__module__": "__main__",
        "__qualname__": "Motor",
    }
    motor = cast(type[SharedBox], type("Motor", (SharedBox,), namespace))
    assert motor._layout_name() == "5b4f7004d44277b7"
    assert motor.__layout__.schema_hash == 0x82CE467598596A72


def test_classes_with_one_identity_share_a_box_by_class() -> None:
    """Check that classes from different modules with one identity attach to the same default box."""
    identity = f"sbtest/reading/{RUN}"
    first = reading_class("package_a.readings", identity)
    second = reading_class("package_b.readings", identity)
    try:
        with first(5), second.attach() as other:
            assert other.value == 5  # type: ignore[attr-defined] # value is a field added dynamically, above
    finally:
        first.unlink()


def test_a_changed_identity_is_refused(unique_name: str) -> None:
    """Check that attaching with a different identity raises SchemaMismatchError."""
    old = reading_class(__name__, "sbtest/reading/1")
    new = reading_class(__name__, "sbtest/reading/2")
    with old.create(unique_name, 1), pytest.raises(SchemaMismatchError):
        new.attach(unique_name)


def test_identity_is_not_inherited() -> None:
    """Check that a subclass gets its own identity and schema hash instead of its parent's."""

    class Base(SharedBox, identity="sbtest/base/1"):
        value: int = 0

    class Child(Base):
        pass

    assert Child.__sharedbox_identity__ == f"{__name__}.{Child.__qualname__}"
    assert Child.__layout__.schema_hash != Base.__layout__.schema_hash


@pytest.mark.parametrize("identity", ["", 3])
def test_identity_must_be_a_non_empty_string(identity: object) -> None:
    """Check that an empty or non-string identity keyword raises TypeError."""
    with pytest.raises(TypeError, match="identity"):

        class Bad(SharedBox, identity=identity):
            value: int = 0


def test_update_names_every_unknown_field_and_writes_nothing(unique_name: str) -> None:
    """Check that update lists every unknown field in one TypeError and writes nothing."""
    with Point.create(unique_name) as box:
        with pytest.raises(TypeError) as error:
            box.update(x=1.0, zz=1, a=2)
        assert str(error.value) == "Point has no field(s) a, zz"
        assert "KeyError" not in "".join(traceback.format_exception(error.value))
        assert box.x == 0.0


def test_update_and_snapshot_on_a_closed_box_raise(unique_name: str) -> None:
    """Check that update and snapshot on a closed box raise BoxClosedError."""
    box = Point.create(unique_name)
    box.close()
    with pytest.raises(BoxClosedError):
        box.update(x=1.0)
    with pytest.raises(BoxClosedError):
        box.snapshot()


class Unordered(SharedBox):
    flag: bool = False
    label: Annotated[str, Capacity(8)] = ""
    count: int = 0


def test_snapshot_keys_follow_declaration_order(unique_name: str) -> None:
    """Check that snapshot lists fields in declaration order."""
    with Unordered.create(unique_name, True, "x", 3) as box:
        assert list(box.snapshot().items()) == [
            ("flag", True),
            ("label", "x"),
            ("count", 3),
        ]


class Large(SharedBox):
    count: int = 0
    blob: Annotated[bytes, Capacity(64 * 1024)] = b""


def test_update_and_snapshot_handle_a_record_larger_than_a_page(
    unique_name: str,
) -> None:
    """Check that update writes and snapshot reads back a record of 64 KiB."""
    blob = bytes(range(256)) * 256
    with Large.create(unique_name) as box:
        box.update(count=7, blob=blob)
        assert box.snapshot() == {"count": 7, "blob": blob}


class Overriding(SharedBox):
    x: float = 0.0
    y: float = 0.0

    def update(self, **values: Any) -> None:
        super().update(**{k: v * 2 for k, v in values.items()})


class BelowOverriding(Overriding):
    pass


class Doubling:
    def update(self, **values: Any) -> None:
        SharedBox.update(cast(SharedBox, self), **{k: v * 2 for k, v in values.items()})


class DoublingFirst(Doubling, SharedBox):
    x: float = 0.0


def test_a_class_that_defines_update_keeps_it_and_its_subclasses_too(
    unique_name: str,
) -> None:
    """Check that update defined on a class, inherited below it or taken from a mixin before SharedBox is the one called."""
    with (
        Overriding.create(f"{unique_name}-o") as o,
        BelowOverriding.create(f"{unique_name}-b") as b,
        DoublingFirst.create(f"{unique_name}-m") as m,
    ):
        o.update(x=1.0)
        b.update(y=2.0)
        m.update(x=3.0)
        assert (o.x, b.y, m.x) == (2.0, 4.0, 6.0)


def test_update_called_on_the_class_or_taken_off_the_box_writes(
    unique_name: str,
) -> None:
    """Check that Point.update(box, ...) and a stored box.update both write."""
    with Point.create(unique_name) as box:
        Point.update(box, x=1.0)
        write = box.update
        write(y=2.0)
        assert (box.x, box.y) == (1.0, 2.0)


def test_update_finds_a_field_whose_name_is_built_at_run_time(
    unique_name: str,
) -> None:
    """Check that a keyword name that is not the interned field name still finds its field."""
    with Point.create(unique_name) as box:
        box.update(**{"LABEL".lower(): "run time"})
        assert box.label == "run time"


def test_update_refuses_positional_arguments(unique_name: str) -> None:
    """Check that update with a positional argument raises TypeError and writes nothing."""
    with Point.create(unique_name) as box:
        with pytest.raises(TypeError):
            box.update(1.0)  # type: ignore[call-arg]
        assert box.x == 0.0


def test_update_with_no_values_changes_nothing(unique_name: str) -> None:
    """Check that update with no arguments returns and leaves every field as it was."""
    with Point.create(unique_name, 1.0, 2.0) as box:
        box.update()
        assert box.snapshot() == {"x": 1.0, "y": 2.0, "label": ""}


def test_update_keeps_no_reference_to_its_values(unique_name: str) -> None:
    """Check that update does not change the reference count of the values it writes."""
    label = "LEAKCHECK".lower()
    with Point.create(unique_name) as box:
        before = sys.getrefcount(label)
        for _ in range(1000):
            box.update(label=label)
        assert sys.getrefcount(label) == before
