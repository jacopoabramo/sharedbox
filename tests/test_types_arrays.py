import array
import multiprocessing as mp
from dataclasses import InitVar, dataclass
from typing import Annotated, Any, Literal, Unpack

import numpy as np
import numpy.typing as npt
import pytest
from crossproc import snapshot_in_child

from sharedbox import (
    Capacity,
    DType,
    Shape,
    SharedBox,
    SharedStream,
    SupportsDLPack,
    field,
    register_array_type,
)
from sharedbox._arrays import CONVERTERS, key
from sharedbox._layout import build_layout


class Frame(SharedBox):
    image: Annotated[np.ndarray, Shape(4, 3), DType("float32")] = np.zeros(
        (4, 3), np.float32
    )
    mask: Annotated[npt.NDArray[np.bool_], Shape(5)] = np.zeros(5, bool)
    raw: Annotated[SupportsDLPack, Shape(2), DType("uint8")] = np.zeros(2, np.uint8)
    big: Annotated[np.ndarray, Shape(1024, 1024), DType("float64")] = np.zeros(
        (1024, 1024)
    )


class Series(SharedBox):
    samples: Annotated[np.ndarray, Shape(3), DType("float64")] = np.zeros(3)
    counts: Annotated[npt.NDArray[np.intc], Shape(2), DType(np.intc)] = np.zeros(
        2, np.intc
    )


@dataclass
class Tile:
    origin: tuple[int, int]
    pixels: Annotated[np.ndarray, Shape(2, 2), DType("int16")]


class Board(SharedBox):
    tile: Tile


def read_in_child(box: Frame, results: "mp.Queue[dict[str, Any]]") -> None:
    values = box.snapshot()
    values["raw"] = np.from_dlpack(values["raw"])
    results.put(values)
    box.close()


def test_arrays_survive_another_process(unique_name: str) -> None:
    """Check that arrays, a non-contiguous source and an 8 MiB one included, read back equal with their dtype in a spawned process."""
    image = np.arange(24, dtype=np.float32).reshape(4, 6)[:, ::2]
    big = np.random.default_rng(1).random((1024, 1024))
    with Frame.create(
        unique_name,
        image=image,
        mask=np.array([1, 0, 1, 0, 1], bool),
        raw=np.array([7, 9], np.uint8),
        big=big,
    ) as box:
        context = mp.get_context("spawn")
        results: mp.Queue[dict[str, Any]] = context.Queue()
        child = context.Process(target=read_in_child, args=(box, results))
        child.start()
        seen = results.get(timeout=60)
        child.join(60)
        assert (
            np.array_equal(seen["image"], image) and seen["image"].dtype == np.float32
        )
        assert np.array_equal(seen["mask"], [True, False, True, False, True])
        assert np.array_equal(seen["raw"], [7, 9])
        assert np.array_equal(seen["big"], big)
    Frame.unlink(unique_name)


def test_a_read_array_is_a_copy(unique_name: str) -> None:
    """Check that changing an array read from the box leaves the stored one as it was."""
    with Frame.create(unique_name) as box:
        image = box.image
        image[0, 0] = 5.0
        assert box.image[0, 0] == 0.0
        assert isinstance(box.raw, SupportsDLPack)
    Frame.unlink(unique_name)


def test_an_array_inside_a_record_reads_back(unique_name: str) -> None:
    """Check that a record with an array member reads back equal."""
    pixels = np.array([[1, -2], [3, 4]], np.int16)
    with Board.create(unique_name, Tile((1, 2), pixels)) as box:
        tile = box.tile
        assert tile.origin == (1, 2)
        assert np.array_equal(tile.pixels, pixels) and tile.pixels.dtype == np.int16
    Board.unlink(unique_name)


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (np.zeros((3, 4), np.float32), ValueError),
        (np.zeros((4, 3), np.float64), TypeError),
        ([[0.0] * 3] * 4, TypeError),
    ],
)
def test_arrays_of_the_wrong_shape_or_dtype_are_refused(
    unique_name: str, value: object, error: type[Exception]
) -> None:
    """Check that the wrong shape raises ValueError, and the wrong dtype or a list raises TypeError, leaving the field as it was."""
    with Frame.create(unique_name) as box:
        with pytest.raises(error, match="Frame.image"):
            box.image = value  # type: ignore[assignment]
        assert not box.image.any()
    Frame.unlink(unique_name)


@pytest.mark.parametrize(
    "source",
    [
        array.array("d", [1.5, -2.0, 3.0]),
        memoryview(array.array("d", [1.5, -2.0, 3.0])),
    ],
)
def test_a_buffer_protocol_source_is_accepted(unique_name: str, source: object) -> None:
    """Check that an object with only the buffer protocol is written and reads back equal."""
    with Series.create(unique_name) as box:
        box.samples = source  # type: ignore[assignment]
        assert np.array_equal(box.samples, [1.5, -2.0, 3.0])
    Series.unlink(unique_name)


def test_a_numpy_scalar_type_is_named_by_its_dtype(unique_name: str) -> None:
    """Check that NDArray[np.intc] and DType(np.intc) make an int32 field that keeps an intc array."""
    with Series.create(unique_name) as box:
        box.counts = np.array([1, -2], np.intc)
        assert np.array_equal(box.counts, [1, -2]) and box.counts.dtype == np.int32
    Series.unlink(unique_name)


class Plain:
    """An array type nothing has registered."""


class Wrapped:
    def __init__(self, array: np.ndarray) -> None:
        self.array = array


def make(annotation: object) -> type:
    return type(
        "T", (), {"__annotations__": {"x": annotation}, "__module__": "__main__"}
    )


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        (Annotated[np.ndarray, DType("float32")], "its sizes"),
        (Annotated[np.ndarray, Shape(2)], "DType"),
        (Annotated[npt.NDArray[np.float64], Shape(2), DType("float32")], "disagree"),
        (Annotated[Plain, Shape(2), DType("float32")], "register_array_type"),
        (
            Annotated[
                list[Annotated[np.ndarray, Shape(2), DType("uint8")]], Capacity(2)
            ],
            "collection element",
        ),
        (Annotated[np.ndarray, Shape(2), DType("float32")] | None, "not optional"),
        (
            Annotated[npt.NDArray[np.object_], Shape(2), DType("float32")],
            "element type",
        ),
    ],
)
def test_array_annotations_that_cannot_be_kept_are_refused(
    annotation: object, message: str
) -> None:
    """Check that a missing Shape or DType, disagreeing dtypes, an unregistered type and an array in a collection or optional raise TypeError."""
    with pytest.raises(TypeError, match=message):
        build_layout(make(annotation))


def test_a_registered_type_reads_back_through_its_converter(unique_name: str) -> None:
    """Check that register_array_type makes a field of that type read back through the given function."""
    register_array_type(Wrapped, lambda array: Wrapped(np.from_dlpack(array)))
    try:
        holder: Any = type(
            "Holder",
            (SharedBox,),
            {
                "__annotations__": {"x": Annotated[Wrapped, Shape(3), DType("int32")]},
                "__module__": __name__,
            },
        )
        with holder.create(unique_name, np.array([1, 2, 3], np.int32)) as box:
            assert isinstance(box.x, Wrapped)
            assert np.array_equal(box.x.array, [1, 2, 3])
        holder.unlink(unique_name)
    finally:
        del CONVERTERS[key(Wrapped)]


@pytest.mark.parametrize("dims", [(), (0,), tuple(range(1, 10))])
def test_shapes_outside_the_limits_are_refused(dims: tuple[int, ...]) -> None:
    """Check that Shape takes 1 to 8 dimensions of at least 1."""
    with pytest.raises(ValueError):
        Shape(*dims)


def zeros(shape: int | tuple[int, ...], dtype: type) -> Any:
    """Return zeros typed Any, for the cases that mismatch a declared dimension count."""
    return np.zeros(shape, dtype)


class ByDefault(SharedBox, identity="sized"):
    image: npt.NDArray[np.float32] = np.zeros((4, 3), np.float32)
    mask: np.ndarray[tuple[int], np.dtype[np.bool_]] = np.zeros(5, bool)


class ByOption(SharedBox, identity="sized"):
    image: npt.NDArray[np.float32] = field(shape=(4, 3))
    mask: np.ndarray[tuple[int], np.dtype[np.bool_]] = field(
        shape=(5,), default=np.zeros(5, bool)
    )


class ByShape(SharedBox, identity="sized"):
    image: Annotated[npt.NDArray[np.float32], Shape(4, 3)] = zeros((4, 3), np.float32)
    mask: Annotated[np.ndarray, Shape(5), DType("bool")] = zeros(5, bool)


WAYS: list[Any] = [ByDefault, ByOption, ByShape]


@dataclass(frozen=True)
class Shot:
    index: int
    pixels: Annotated[npt.NDArray[np.float64], Shape(4)]


@dataclass(frozen=True)
class Labelled:
    shape: Annotated[tuple[Annotated[str, Capacity(4)], ...], Capacity(2)] = ("a",)


class Holder(SharedBox):
    label: Labelled = Labelled()


class Shaped:
    """An object with a shape that is no tuple of ints."""

    shape = (None,)


@pytest.mark.parametrize("way", WAYS)
def test_each_way_to_give_sizes_reads_back_in_another_process(
    unique_name: str, way: Any
) -> None:
    """Check that sizes from a default, field(shape=) or a Shape each read back equal in a spawned process."""
    image = np.arange(12, dtype=np.float32).reshape(4, 3)
    with way.create(unique_name, image=image) as box:
        seen = snapshot_in_child(box)
        assert np.array_equal(seen["image"], image)
        assert seen["image"].dtype == np.float32 and seen["image"].shape == (4, 3)
        assert seen["mask"].dtype == np.bool_ and seen["mask"].shape == (5,)
    way.unlink(unique_name)


def test_the_ways_to_give_sizes_describe_the_same_field(unique_name: str) -> None:
    """Check that the three ways give one schema hash, so each attaches a box created with another."""
    assert len({way.__layout__.schema_hash for way in WAYS}) == 1
    image = np.arange(12, dtype=np.float32).reshape(4, 3)
    with ByDefault.create(unique_name, image=image):
        for way in (ByOption, ByShape):
            with way.attach(unique_name) as other:
                assert np.array_equal(other.image, image)
    ByDefault.unlink(unique_name)


def test_agreeing_sizes_are_accepted() -> None:
    """Check that a Shape, field(shape=) and a default array of one shape build the plain Shape and DType layout."""

    class All(SharedBox, identity="sized"):
        image: Annotated[npt.NDArray[np.float32], Shape(4, 3)] = field(
            shape=(4, 3), default=zeros((4, 3), np.float32)
        )
        mask: Annotated[np.ndarray, Shape(5), DType("bool")] = zeros(5, bool)

    assert All.__layout__.schema_hash == ByShape.__layout__.schema_hash


def test_sizes_from_two_places_that_differ_are_refused() -> None:
    """Check that each pair of a Shape, field(shape=) and a default array that differ raises TypeError naming the field and both."""
    with pytest.raises(
        TypeError, match=r"Pair.x: Shape\(2, 3\) and field\(shape=\(3, 2\)\) disagree"
    ):

        class Pair(SharedBox):
            x: Annotated[npt.NDArray[np.uint8], Shape(2, 3)] = field(shape=(3, 2))

    with pytest.raises(
        TypeError, match=r"Pair.x: Shape\(2, 3\) and the default array's shape \(3, 2\)"
    ):

        class Pair(SharedBox):  # type: ignore[no-redef]
            x: Annotated[npt.NDArray[np.uint8], Shape(2, 3)] = zeros((3, 2), np.uint8)

    with pytest.raises(
        TypeError,
        match=r"Pair.x: field\(shape=\(2, 3\)\) and the default array's shape \(3, 2\)",
    ):

        class Pair(SharedBox):  # type: ignore[no-redef]
            x: npt.NDArray[np.uint8] = field(
                shape=(2, 3), default=zeros((3, 2), np.uint8)
            )


@pytest.mark.parametrize("shape", [(), (0,), (1,) * 9, (1.5,), (True,)])
def test_bad_field_shapes_are_refused(shape: tuple[Any, ...]) -> None:
    """Check that field(shape=) refuses the sizes Shape refuses, with a ValueError."""
    with pytest.raises(ValueError):
        field(shape=shape)


def test_a_field_shape_that_is_not_a_sequence_is_refused() -> None:
    """Check that field(shape=4) raises TypeError."""
    with pytest.raises(TypeError):
        field(shape=4)  # type: ignore[arg-type]


def test_a_field_shape_on_a_field_that_is_not_an_array_is_refused() -> None:
    """Check that field(shape=) on an int field raises TypeError naming the field."""
    with pytest.raises(TypeError, match=r"Bad.x: field\(shape=\.\.\.\) applies only"):

        class Bad(SharedBox):
            x: int = field(shape=(2,))


def test_a_non_array_default_with_a_shape_attribute_is_left_alone(
    unique_name: str,
) -> None:
    """Check that a record whose shape member holds strings is accepted and reads back."""
    with Holder.create(unique_name) as box:
        assert box.label == Labelled()
    Holder.unlink(unique_name)


def test_an_array_default_with_an_odd_shape_attribute_is_refused() -> None:
    """Check that a default with shape=(None,) raises TypeError, next to a Shape and alone."""
    with pytest.raises(TypeError):

        class WithShape(SharedBox):
            x: Annotated[npt.NDArray[np.uint8], Shape(1)] = Shaped()  # type: ignore[assignment]

    with pytest.raises(TypeError, match="its sizes"):

        class Alone(SharedBox):
            x: npt.NDArray[np.uint8] = Shaped()  # type: ignore[assignment]


@pytest.mark.parametrize("value", [np.zeros(()), np.zeros((0, 3))])
def test_a_zero_dimensional_or_empty_default_is_refused(value: Any) -> None:
    """Check that a 0-d or zero-size default raises TypeError naming the field and its shape."""
    with pytest.raises(TypeError, match=r"Empty.x: the default array has shape"):

        class Empty(SharedBox):
            x: npt.NDArray[np.float64] = value


def test_the_dimension_count_of_the_annotation_is_checked() -> None:
    """Check that a Shape, field(shape=) and a default array of another dimension count than tuple[int, int] raise TypeError naming both."""
    with pytest.raises(TypeError, match=r"2 dimensions and Shape\(4, 6, 3\) has 3"):
        build_layout(
            make(
                Annotated[
                    np.ndarray[tuple[int, int], np.dtype[np.uint8]], Shape(4, 6, 3)
                ]
            )
        )

    for open_ended in (
        np.ndarray[tuple[int, *tuple[int, ...]], np.dtype[np.uint8]],
        np.ndarray[tuple[int, Unpack[tuple[int, ...]]], np.dtype[np.uint8]],  # noqa: UP044
    ):
        build_layout(make(Annotated[open_ended, Shape(4, 6, 3)]))

    with pytest.raises(
        TypeError, match=r"2 dimensions and field\(shape=\(2,\)\) has 1"
    ):

        class A(SharedBox):
            x: np.ndarray[tuple[int, int], np.dtype[np.uint8]] = field(shape=(2,))

    with pytest.raises(TypeError, match="2 dimensions and the default array's shape"):

        class B(SharedBox):
            x: np.ndarray[tuple[int, int], np.dtype[np.uint8]] = zeros(
                (2, 3, 4), np.uint8
            )


def test_literal_dimensions_count_like_int() -> None:
    """Check that Literal dimensions name no size but count as dimensions."""
    literal = np.ndarray[tuple[Literal[4], Literal[6]], np.dtype[np.uint8]]
    with pytest.raises(TypeError, match="its sizes"):
        build_layout(make(literal))
    build_layout(make(Annotated[literal, Shape(4, 6)]))
    with pytest.raises(TypeError, match="2 dimensions"):
        build_layout(make(Annotated[literal, Shape(4, 6, 3)]))


def test_a_default_factory_does_not_give_sizes() -> None:
    """Check that default_factory alone raises TypeError saying where sizes come from."""
    with pytest.raises(TypeError, match="its sizes"):

        class Factory(SharedBox):
            x: npt.NDArray[np.uint8] = field(default_factory=lambda: zeros(3, np.uint8))


def test_a_field_shape_without_a_default_leaves_the_field_required(
    unique_name: str,
) -> None:
    """Check that field(shape=) alone makes the field a required constructor argument."""

    class Needs(SharedBox):
        x: npt.NDArray[np.uint8] = field(shape=(3,))

    with pytest.raises(TypeError):
        Needs()  # type: ignore[call-arg]
    with Needs.create(unique_name, np.arange(3, dtype=np.uint8)) as box:
        assert box.x.shape == (3,)
    Needs.unlink(unique_name)


def test_a_subclass_default_changes_the_shape_unless_its_sizes_are_repeated() -> None:
    """Check that a new default array changes an inherited field's shape, a bare redeclaration keeps the base's default, and sizes repeated in the subclass's declaration make a differing default raise."""

    class Base(SharedBox):
        x: npt.NDArray[np.uint8] = zeros(3, np.uint8)

    class Wider(Base):
        x: npt.NDArray[np.uint8] = zeros(5, np.uint8)

    class Bare(Base):
        x: npt.NDArray[np.uint8]

    assert Base.__layout__.by_name["x"].capacity == 3
    assert Wider.__layout__.by_name["x"].capacity == 5
    assert Bare.__layout__.by_name["x"].capacity == 3

    class Pinned(SharedBox):
        x: Annotated[npt.NDArray[np.uint8], Shape(3)] = zeros(3, np.uint8)

    class Repinned(Pinned):
        x: npt.NDArray[np.uint8] = zeros(5, np.uint8)

    assert Repinned.__layout__.by_name["x"].capacity == 5
    with pytest.raises(TypeError, match="disagree"):

        class ShapeChanged(Pinned):
            x: Annotated[npt.NDArray[np.uint8], Shape(3)] = zeros(5, np.uint8)

    with pytest.raises(TypeError, match="disagree"):

        class OptionChanged(Pinned):
            x: npt.NDArray[np.uint8] = field(shape=(3,), default=zeros(5, np.uint8))


def test_an_inherited_field_shape_option_is_kept_unless_the_field_is_redeclared() -> (
    None
):
    """Check that a subclass that leaves the field alone keeps field(shape=), and a bare redeclaration loses it and needs sizes."""

    class OptOnly(SharedBox):
        x: npt.NDArray[np.uint8] = field(shape=(3,))

    class Inherits(OptOnly):
        pass

    assert Inherits.__layout__.by_name["x"].capacity == 3
    with pytest.raises(TypeError, match="its sizes"):

        class Sub(OptOnly):
            x: npt.NDArray[np.uint8]


def test_a_plain_attribute_over_an_inherited_array_field_keeps_the_old_message() -> (
    None
):
    """Check that overriding an inherited array field with an unannotated attribute says so, not that the shapes disagree."""

    class Pinned(SharedBox):
        x: Annotated[npt.NDArray[np.uint8], Shape(3)] = zeros(3, np.uint8)

    with pytest.raises(TypeError, match="overrides the field inherited from"):

        class Sub(Pinned):
            x = zeros(5, np.uint8)


def test_field_shape_is_refused_on_a_reference_and_an_init_var() -> None:
    """Check that field(shape=) on a reference field, an optional one and an InitVar raises TypeError."""

    class Target(SharedBox):
        n: int = 0

    with pytest.raises(TypeError, match=r"Plain.t: field\(shape=\.\.\.\) applies only"):

        class Plain(SharedBox):
            t: Target = field(shape=(2,))

    with pytest.raises(TypeError, match=r"Maybe.t: field\(shape=\.\.\.\) applies only"):

        class Maybe(SharedBox):
            t: Target | None = field(shape=(2,), default=None)

    with pytest.raises(TypeError, match=r"Init.k: field\(shape=\.\.\.\) applies only"):

        class Init(SharedBox):
            k: InitVar[int] = field(shape=(3,))
            n: int = 0

            def __post_init__(self, k: int) -> None:
                pass


def test_where_sizes_can_come_from_is_named_by_position(unique_name: str) -> None:
    """Check that a box field names all three ways, and a record member and a stream item name only Shape."""
    with pytest.raises(TypeError, match=r"field\(shape=.*default array"):
        build_layout(make(npt.NDArray[np.uint8]))

    @dataclass(frozen=True)
    class Member:
        pixels: npt.NDArray[np.uint8]

    with pytest.raises(TypeError) as member:
        build_layout(make(Member))
    assert "Shape" in str(member.value) and "field(shape" not in str(member.value)

    with pytest.raises(TypeError) as item:
        SharedStream.create(Member, unique_name, capacity=2)
    assert "Shape" in str(item.value) and "field(shape" not in str(item.value)


def test_a_stream_item_with_a_shape_in_the_annotation_sends_and_receives(
    unique_name: str,
) -> None:
    """Check that an item whose array member is annotated NDArray[float64] with a Shape goes through a stream, receive_into included."""
    with SharedStream.create(Shot, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()
        pixels: Any = np.arange(4, dtype=np.float64)
        sender.send(Shot(1, pixels))
        sender.send(Shot(2, pixels + 1))
        assert np.array_equal(reader.receive().pixels, pixels)
        target = np.zeros(4, np.float64)
        got = reader.receive_into({"pixels": target})
        assert got.pixels is target and got.index == 2
        assert np.array_equal(target, pixels + 1)
