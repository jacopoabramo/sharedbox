import time
import weakref
from collections.abc import Callable
from dataclasses import MISSING, dataclass
from typing import Annotated, Any, Optional

import numpy as np
import numpy.typing as npt
import pytest

from sharedbox import (
    Attr,
    BoxClosedError,
    Capacity,
    DType,
    MaxShape,
    Setting,
    Shape,
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
VECTOR = Annotated[npt.NDArray[np.float64], Shape((4,))]


@dataclass(frozen=True)
class Point:
    x: int
    y: int


class Plain(SharedBox):
    count: int = 0


class Camera(SharedBox):
    temperature: Attr[float] = attr()
    exposure: Setting[float] = attr()
    move: Attr[float, [float, Annotated[str, Capacity(16)]]] = attr()
    home: Attr[None] = attr()


class Recipe(SharedBox):
    mix: Setting[Point] = attr(default=Point(1, 2))


class Frames(SharedBox):
    image: Setting[Annotated[np.ndarray, MaxShape((4, 4)), DType(np.uint8)]] = attr()


class Megapixels(SharedBox):
    image: Attr[Annotated[np.ndarray, MaxShape((1024, 1024)), DType(np.uint8)]] = attr()


@dataclass
class Holder:
    inner: Attr[float]


def test_an_attr_starts_at_zero(names: Callable[[str], str]) -> None:
    """Read zero values and timestamps from a new box."""
    with Camera.create(names("zero")) as box:
        assert box.exposure.read() == {"value": 0.0, "setpoint": 0.0, "timestamp_ns": 0}
        assert box.temperature.read() == {"value": 0.0, "timestamp_ns": 0}
        assert box.move.read() == {"value": 0.0, "timestamp_ns": 0}


def test_an_attr_starts_at_its_default(names: Callable[[str], str]) -> None:
    """Give the value and the setpoint the default of the field."""
    with Recipe.create(names("default")) as box:
        assert box.mix.read() == {
            "value": Point(1, 2),
            "setpoint": Point(1, 2),
            "timestamp_ns": 0,
        }


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


def test_publish_takes_a_numpy_integer_as_the_timestamp(
    names: Callable[[str], str],
) -> None:
    """Store a numpy int64 timestamp as its integer value."""
    with Camera.create(names("numpy-stamp")) as box:
        box.temperature.publish(value=1.5, timestamp_ns=np.int64(5))
        assert box.temperature.timestamp_ns == 5


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


def test_a_megabyte_attr_publishes_and_reads_back(names: Callable[[str], str]) -> None:
    """Round-trip an array of a mebibyte through publish, read and a watch future."""
    value = np.random.default_rng(0).integers(0, 256, (1024, 1024), dtype=np.uint8)
    with Megapixels.create(names("megabyte")) as box:
        fut = box.watch("image").future()
        box.image.publish(value=value)
        np.testing.assert_array_equal(box.image.read()["value"], value)
        np.testing.assert_array_equal(fut.result(timeout=5)["value"], value)


@pytest.mark.parametrize(
    "publish",
    [
        pytest.param(lambda box: box.temperature.publish(), id="nothing"),
        pytest.param(
            lambda box: box.temperature.publish(timestamp_ns=1), id="time-alone"
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


@pytest.mark.parametrize(
    "use",
    [
        pytest.param(lambda box: box.temperature.setpoint, id="setpoint"),
        pytest.param(lambda box: box.temperature.set(1.0), id="set"),
    ],
)
def test_a_plain_attr_has_no_setpoint(
    names: Callable[[str], str], use: Callable[[Camera], object]
) -> None:
    """Raise AttributeError for the setpoint and set() of an attribute that is not a Setting."""
    with Camera.create(names("plain")) as box, pytest.raises(AttributeError):
        use(box)


def test_a_plain_attr_publishes_no_setpoint(names: Callable[[str], str]) -> None:
    """Raise TypeError for publish(setpoint=...) on a plain Attr and leave its value as it was."""
    with Camera.create(names("nosetpoint")) as box:
        box.temperature.publish(value=2.0)
        with pytest.raises(TypeError, match="setpoint"):
            box.temperature.publish(value=3.0, setpoint=3.0)  # type: ignore[call-arg]
        assert box.temperature.value == 2.0


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
        assert id(box.exposure) != id(box.temperature)


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
        pytest.param(Setting[None], attr(), id="settable-command"),
        pytest.param(Setting, attr(), id="bare-setting"),
        pytest.param(Setting[float], MISSING, id="setting-without-attr"),
        pytest.param(Setting[Timed[float]], attr(), id="timed-setting"),
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


def test_a_setting_takes_no_argument_types() -> None:
    """Raise TypeError for a Setting subscripted with argument types."""
    with pytest.raises(TypeError):
        Setting[float, [float]]  # type: ignore[misc, valid-type]


@pytest.mark.parametrize("option", ["timed", "settable"])
def test_attr_takes_no_storage_option(option: str) -> None:
    """Raise TypeError for the removed timed and settable options of attr()."""
    removed: dict[str, Any] = {option: True}
    with pytest.raises(TypeError, match=option):
        attr(**removed)


def test_every_attr_with_a_value_is_timed(names: Callable[[str], str]) -> None:
    """Stamp a value published without a time on an Attr declared with a plain attr()."""
    with Camera.create(names("timed")) as box:
        before = time.time_ns()
        box.move.publish(value=1.0)
        assert box.move.timestamp_ns >= before - CLOCK_SLACK_NS
        move_type = Camera.__layout__.by_name["move"].type
        assert move_type is not None and move_type.text.startswith("attr(timed,")


def test_a_settable_field_gives_a_setting(names: Callable[[str], str]) -> None:
    """Give a Setting handle for a Setting field and a plain Attr for the others."""
    with Camera.create(names("kinds")) as box:
        assert type(box.exposure) is Setting
        assert isinstance(box.exposure, Attr)
        assert type(box.temperature) is Attr


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
        Optional[Setting[float]],  # noqa: UP045
        Annotated[list[Setting[float]], Capacity(2)],
    ]
    for holder in holders:
        with pytest.raises(TypeError, match=r"Declared\.x"):
            define(holder, MISSING)
    with pytest.raises(TypeError):
        SharedStream.create(Attr[float], capacity=2)
    with pytest.raises(TypeError):
        SharedStream.create(Setting[float], capacity=2)


def test_the_python_layout_matches_the_native_parser() -> None:
    """Check the size of each attr shape, 64-aligned values too, against the native parser."""
    shapes = [
        (16, int, (), False, False, False),
        (24, int, (), True, False, False),
        (1192, int, (int,), False, False, True),
        (1184, int, (), False, False, True),
        (1200, int, (int,), False, True, True),
        (1208, int, (int,), True, True, True),
        (1136, None, (), False, False, True),
        (1792, VECTOR, (bool, VECTOR, np.int16), True, False, True),
        (1856, VECTOR, (VECTOR,), True, True, True),
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
