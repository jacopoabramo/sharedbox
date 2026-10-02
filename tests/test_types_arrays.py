import array
import multiprocessing as mp
from dataclasses import dataclass
from typing import Annotated, Any

import numpy as np
import numpy.typing as npt
import pytest

from sharedbox import (
    Capacity,
    DType,
    Shape,
    SharedBox,
    SupportsDLPack,
    register_array_type,
)
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


@pytest.mark.parametrize("dims", [(), (0,), tuple(range(1, 10))])
def test_shapes_outside_the_limits_are_refused(dims: tuple[int, ...]) -> None:
    """Check that Shape takes 1 to 8 dimensions of at least 1."""
    with pytest.raises(ValueError):
        Shape(*dims)
