import array
import multiprocessing as mp
from dataclasses import dataclass
from typing import Annotated, Any, Literal

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
        (Annotated[np.ndarray, DType("float32")], "one Shape"),
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
    """Return zeros typed Any, since numpy types a shape argument as `tuple[int, ...]`."""
    return np.zeros(shape, dtype)


class Native(SharedBox, identity="native-shape"):
    image: np.ndarray[tuple[Literal[4], Literal[3]], np.dtype[np.float32]] = zeros(
        (4, 3), np.float32
    )
    mask: np.ndarray[tuple[Literal[5]], np.dtype[np.bool_]] = zeros(5, bool)


class Spelled(SharedBox, identity="native-shape"):
    image: Annotated[np.ndarray, Shape(4, 3), DType("float32")] = np.zeros(
        (4, 3), np.float32
    )
    mask: Annotated[npt.NDArray[np.bool_], Shape(5)] = np.zeros(5, bool)


@dataclass(frozen=True)
class Shot:
    index: int
    pixels: np.ndarray[tuple[Literal[2], Literal[3]], np.dtype[np.uint16]]


def test_a_shape_and_dtype_in_the_annotation_read_back_in_another_process(
    unique_name: str,
) -> None:
    """Check that a field declared only by its annotation reads back equal in a spawned process."""
    image = np.arange(12, dtype=np.float32).reshape(4, 3)
    with Native.create(unique_name, image=image) as box:
        seen = snapshot_in_child(box)
        assert np.array_equal(seen["image"], image)
        assert seen["image"].dtype == np.float32 and seen["image"].shape == (4, 3)
        assert seen["mask"].dtype == np.bool_ and seen["mask"].shape == (5,)
    Native.unlink(unique_name)


def test_both_spellings_describe_the_same_field(unique_name: str) -> None:
    """Check that the annotation form and the Shape and DType form give one schema hash, so either attaches the other's box."""
    assert Native.__layout__.schema_hash == Spelled.__layout__.schema_hash
    image = np.arange(12, dtype=np.float32).reshape(4, 3)
    with Native.create(unique_name, image=image) as box:
        with Spelled.attach(unique_name) as other:
            assert np.array_equal(other.image, image)
            other.image = image + 1
        assert np.array_equal(box.image, image + 1)
    Native.unlink(unique_name)


def test_a_shape_that_agrees_with_the_annotation_is_accepted() -> None:
    """Check that a Shape equal to the literals in the annotation builds the same layout."""
    agreeing = make(
        Annotated[
            np.ndarray[tuple[Literal[2], Literal[3]], np.dtype[np.uint8]], Shape(2, 3)
        ]
    )
    plain = make(
        np.ndarray[tuple[Literal[2], Literal[3]], np.dtype[np.uint8]],
    )
    assert build_layout(agreeing).schema_hash == build_layout(plain).schema_hash


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        (
            Annotated[
                np.ndarray[tuple[Literal[2], Literal[3]], np.dtype[np.uint8]],
                Shape(3, 2),
            ],
            "disagree",
        ),
        (np.ndarray[tuple[int, int], np.dtype[np.uint8]], "Shape"),
        (np.ndarray[tuple[int, ...], np.dtype[np.uint8]], "Shape"),
        (np.ndarray[tuple[Literal[2], int], np.dtype[np.uint8]], "Shape"),
        (np.ndarray[tuple[Literal[1, 2]], np.dtype[np.uint8]], "Shape"),
        (npt.NDArray[np.uint8], "Shape"),
        (np.ndarray[tuple[Literal[0]], np.dtype[np.uint8]], "Literal.0."),
        (np.ndarray[tuple[Literal[2], Literal[-3]], np.dtype[np.uint8]], "-3"),
        (np.ndarray[tuple[Literal[True]], np.dtype[np.uint8]], "True"),
        (
            np.ndarray[tuple[Literal["a"]], np.dtype[np.uint8]],  # type: ignore[type-var]
            "'a'",
        ),
    ],
)
def test_annotation_shapes_that_cannot_be_kept_are_refused(
    annotation: object, message: str
) -> None:
    """Check that a disagreeing Shape, a dimension that names no size and a literal that is not a positive int raise TypeError."""
    with pytest.raises(TypeError, match=message):
        build_layout(make(annotation))


def test_a_stream_item_with_an_annotated_array_sends_and_receives(
    unique_name: str,
) -> None:
    """Check that an item whose array member names its shape and dtype only in the annotation goes through a stream, receive_into included."""
    with SharedStream.create(Shot, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()
        pixels: Any = np.arange(6, dtype=np.uint16).reshape(2, 3)
        sender.send(Shot(1, pixels))
        sender.send(Shot(2, pixels + 1))
        assert np.array_equal(reader.receive().pixels, pixels)
        target = np.zeros((2, 3), np.uint16)
        got = reader.receive_into({"pixels": target})
        assert got.pixels is target and got.index == 2
        assert np.array_equal(target, pixels + 1)
