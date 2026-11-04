import time
import weakref
from collections.abc import Callable
from dataclasses import MISSING, dataclass
from typing import Annotated, Any, Optional

import numpy as np
import pytest

from sharedbox import (
    Attr,
    BoxClosedError,
    Capacity,
    DType,
    MaxShape,
    SharedBox,
    SharedStream,
    Timed,
    attr,
    field,
)
from sharedbox._layout import NativeField
from sharedbox._native import Types
from sharedbox._types import CODES, Table, attr_spec

CLOCK_SLACK_NS = 20_000_000


@dataclass(frozen=True)
class Point:
    x: int
    y: int


class Plain(SharedBox):
    count: int = 0


class Camera(SharedBox):
    temperature: Attr[float] = attr(timed=True)
    exposure: Attr[float] = attr(timed=True, settable=True)
    move: Attr[float, [float, Annotated[str, Capacity(16)]]] = attr()
    home: Attr[None] = attr()


class Recipe(SharedBox):
    mix: Attr[Point] = attr(default=Point(1, 2), settable=True)


class Frames(SharedBox):
    image: Attr[Annotated[np.ndarray, MaxShape((4, 4)), DType(np.uint8)]] = attr(
        settable=True
    )


@dataclass
class Holder:
    inner: Attr[float]


def test_an_attr_starts_at_zero(names: Callable[[str], str]) -> None:
    """Read zero values and timestamps from a new box."""
    with Camera.create(names("zero")) as box:
        assert box.exposure.read() == {"value": 0.0, "setpoint": 0.0, "timestamp_ns": 0}
        assert box.temperature.read() == {"value": 0.0, "timestamp_ns": 0}
        assert box.move.read() == {"value": 0.0}


def test_an_attr_starts_at_its_default(names: Callable[[str], str]) -> None:
    """Give the value and the setpoint the default of the field."""
    with Recipe.create(names("default")) as box:
        assert box.mix.read() == {"value": Point(1, 2), "setpoint": Point(1, 2)}


def test_publish_writes_the_parts_it_is_given(names: Callable[[str], str]) -> None:
    """Write the named parts, stamp a value given without a time, and keep the rest."""
    with Camera.create(names("publish")) as box:
        before = time.time_ns()
        box.temperature.publish(value=21.5)
        after = time.time_ns() + CLOCK_SLACK_NS
        read = box.temperature.read()
        assert read["value"] == 21.5
        assert before <= read["timestamp_ns"] <= after

        box.temperature.publish(value=22.5, timestamp_ns=7)
        assert box.temperature.read() == {"value": 22.5, "timestamp_ns": 7}

        box.exposure.publish(value=1.0, timestamp_ns=5)
        box.exposure.publish(setpoint=0.02)
        assert box.exposure.read() == {
            "value": 1.0,
            "setpoint": 0.02,
            "timestamp_ns": 5,
        }
        assert box.exposure.value == 1.0
        assert box.exposure.setpoint == 0.02
        assert box.exposure.timestamp_ns == 5


def test_publish_replaces_the_timed_mark_with_the_time_of_the_write(
    names: Callable[[str], str],
) -> None:
    """Read back a real time for a timestamp given as -2**63."""
    with Camera.create(names("mark")) as box:
        before = time.time_ns()
        box.temperature.publish(value=1.5, timestamp_ns=-(2**63))
        after = time.time_ns() + CLOCK_SLACK_NS
        assert before <= box.temperature.timestamp_ns <= after


def test_a_refused_publish_writes_nothing(names: Callable[[str], str]) -> None:
    """Leave the box as it was when a named part does not encode."""
    with Camera.create(names("refused")) as box:
        box.exposure.publish(value=1.0, setpoint=2.0, timestamp_ns=3)
        index = Camera.__layout__.by_name["exposure"].index
        before = box.exposure.read()
        published = box._segment.attr_published(index)
        with pytest.raises(TypeError, match="exposure"):
            box.exposure.publish(value=5.0, setpoint="x")  # type: ignore[arg-type]
        assert box.exposure.read() == before
        assert box._segment.attr_published(index) == published


def test_a_max_shape_attr_keeps_the_shape_of_each_part(
    names: Callable[[str], str],
) -> None:
    """Read back the value and the setpoint with the shapes they were published with."""
    with Frames.create(names("frames")) as box:
        value = np.arange(6, dtype=np.uint8).reshape(2, 3)
        setpoint = np.full((4, 1), 9, dtype=np.uint8)
        box.image.publish(value=value, setpoint=setpoint)
        read = box.image.read()
        assert read["value"].shape == (2, 3)
        assert read["setpoint"].shape == (4, 1)
        np.testing.assert_array_equal(read["value"], value)
        np.testing.assert_array_equal(read["setpoint"], setpoint)


@pytest.mark.parametrize(
    "publish",
    [
        pytest.param(lambda box: box.temperature.publish(), id="nothing"),
        pytest.param(
            lambda box: box.temperature.publish(timestamp_ns=1), id="time-alone"
        ),
        pytest.param(
            lambda box: box.temperature.publish(setpoint=1.0), id="not-settable"
        ),
        pytest.param(
            lambda box: box.move.publish(timestamp_ns=1, value=1.0), id="not-timed"
        ),
        pytest.param(lambda box: box.home.publish(value=None), id="no-value"),
    ],
)
def test_publish_refuses_bad_parts(
    names: Callable[[str], str], publish: Callable[[Camera], object]
) -> None:
    """Raise TypeError naming the field for parts the attribute lacks or that cannot stand alone."""
    with (
        Camera.create(names("bad")) as box,
        pytest.raises(TypeError, match=r"Camera\.(temperature|move|home)"),
    ):
        publish(box)


@pytest.mark.parametrize(
    "read",
    [
        pytest.param(lambda box: box.home.value, id="home-value"),
        pytest.param(lambda box: box.temperature.setpoint, id="setpoint"),
        pytest.param(lambda box: box.move.timestamp_ns, id="timestamp"),
        pytest.param(lambda box: box.home.read(), id="home-read"),
    ],
)
def test_the_handle_refuses_parts_the_attr_lacks(
    names: Callable[[str], str], read: Callable[[Camera], object]
) -> None:
    """Raise TypeError naming the field for a part the attribute does not have."""
    with (
        Camera.create(names("lacks")) as box,
        pytest.raises(TypeError, match=r"Camera\.(temperature|move|home)"),
    ):
        read(box)


def assign(box: Camera) -> None:
    box.exposure = 1.0  # type: ignore[assignment]


def delete(box: Camera) -> None:
    del box.exposure


def update(box: Camera) -> None:
    box.update(exposure=1.0)


def write_in_place(box: Camera) -> None:
    with box.writing("exposure"):
        pass


@pytest.mark.parametrize("operation", [assign, delete, update, write_in_place])
def test_an_attr_refuses_value_operations(
    names: Callable[[str], str], operation: Callable[[Camera], None]
) -> None:
    """Raise TypeError naming the field for assigning, deleting, updating or writing in place."""
    with (
        Camera.create(names("operations")) as box,
        pytest.raises(TypeError, match=r"Camera\.exposure"),
    ):
        operation(box)


def test_the_handle_is_the_same_object_on_every_access(
    names: Callable[[str], str],
) -> None:
    """Return one handle per field for the life of the box."""
    with Camera.create(names("same")) as box:
        assert box.exposure is box.exposure
        assert box.exposure is not box.temperature


def test_snapshot_holds_attr_values_and_leaves_out_commands(
    names: Callable[[str], str],
) -> None:
    """Give each attribute with a value its read() and leave out one without."""
    with Camera.create(names("snapshot")) as box:
        box.temperature.publish(value=3.0, timestamp_ns=1)
        snapshot = box.snapshot()
        assert snapshot == {
            "temperature": box.temperature.read(),
            "exposure": box.exposure.read(),
            "move": box.move.read(),
        }
        assert snapshot["temperature"] == {"value": 3.0, "timestamp_ns": 1}


def test_an_attached_box_reads_what_the_owner_published(
    names: Callable[[str], str],
) -> None:
    """Read the published value through another handle on the segment."""
    name = names("attached")
    with Camera.create(name) as owner, Camera.attach(name) as reader:
        owner.exposure.publish(value=0.5, setpoint=0.25, timestamp_ns=11)
        assert reader.exposure.read() == {
            "value": 0.5,
            "setpoint": 0.25,
            "timestamp_ns": 11,
        }


def test_a_handle_of_a_closed_box_raises(names: Callable[[str], str]) -> None:
    """Raise BoxClosedError from a handle taken before the box was closed."""
    box = Camera.create(names("closed"))
    handle = box.exposure
    box.close()
    with pytest.raises(BoxClosedError):
        handle.read()
    with pytest.raises(BoxClosedError):
        handle.publish(value=1.0)


def define(annotation: Any, value: Any) -> type[SharedBox]:
    namespace: dict[str, Any] = {"__annotations__": {"x": annotation}}
    if value is not MISSING:
        namespace["x"] = value
    return type("Declared", (SharedBox,), namespace)


@pytest.mark.parametrize(
    ("annotation", "value"),
    [
        pytest.param(Attr[float], MISSING, id="no-attr"),
        pytest.param(Attr[float], field(init=False), id="field"),
        pytest.param(float, attr(), id="attr-on-a-float"),
        pytest.param(Attr[None], attr(timed=True), id="timed-command"),
        pytest.param(Attr[None], attr(settable=True), id="settable-command"),
        pytest.param(
            Attr[float, [float]], attr(settable=True), id="settable-with-arguments"
        ),
        pytest.param(Attr[float, ...], attr(), id="ellipsis-arguments"),
        pytest.param(Attr[float], attr(default="x"), id="bad-default"),
        pytest.param(Attr[None], attr(default=1), id="default-without-value"),
        pytest.param(Attr[Point], attr(), id="no-zero"),
        pytest.param(
            Attr[float],
            attr(init=True),  # type: ignore[arg-type]
            id="init",
        ),
    ],
)
def test_an_attr_declaration_is_refused(annotation: Any, value: Any) -> None:
    """Raise TypeError naming the field when the class is defined."""
    with pytest.raises(TypeError, match=r"Declared\.x"):
        define(annotation, value)


def test_a_timed_or_reference_value_is_refused() -> None:
    """Raise TypeError for a Timed value and for a box reference as an argument."""
    with pytest.raises(TypeError, match=r"Declared\.x"):
        define(Attr[Timed[float]], attr())
    with pytest.raises(TypeError, match=r"Declared\.x"):
        define(Attr[None, [Plain]], attr())


def test_attr_is_refused_where_it_cannot_go() -> None:
    """Raise TypeError for an Attr inside a record, an optional, a union, a list or a stream item."""
    holders: list[Any] = [
        Holder,
        Optional[Attr[float]],  # noqa: UP045
        Attr[float] | int,
        Annotated[list[Attr[float]], Capacity(2)],
    ]
    for holder in holders:
        with pytest.raises(TypeError, match=r"Declared\.x"):
            define(holder, MISSING)
    with pytest.raises(TypeError):
        SharedStream.create(Attr[float], capacity=2)


def test_the_python_layout_matches_the_native_parser() -> None:
    """Build each shape of attr the native tests pin and check its size, which the native parser also checks."""
    shapes = [
        (16, int, (), False, False, False),
        (24, int, (), True, False, False),
        (1192, int, (int,), False, False, True),
        (1184, int, (), False, False, True),
        (1200, int, (int,), False, True, True),
        (1208, int, (int,), True, True, True),
        (1136, None, (), False, False, True),
    ]
    for size, value, arguments, timed, settable, request in shapes:
        spec = attr_spec(
            value,
            arguments,
            "shape",
            frozenset(),
            timed=timed,
            settable=settable,
            request=request,
        )
        assert spec.size == size
        table = Table()
        description = table.describe(spec)
        Types(
            [NativeField(0, description, CODES["attr"])],
            ["shape"],
            bytes(table.data),
            table.info,
            table.bytearrays,
            [],
            table.converts,
        )


def test_a_handle_does_not_keep_its_box_alive(names: Callable[[str], str]) -> None:
    """Free a box that was read through a handle as soon as its last reference goes."""
    box = Camera.create(names("weak"))
    box.exposure.read()
    ref = weakref.ref(box)
    del box
    assert ref() is None


def test_a_handle_of_a_freed_box_raises(names: Callable[[str], str]) -> None:
    """Raise BoxClosedError from a handle kept after its box was freed."""
    box = Camera.create(names("freed"))
    handle = box.exposure
    del box
    with pytest.raises(BoxClosedError):
        handle.read()
    with pytest.raises(BoxClosedError):
        handle.publish(value=1.0)
