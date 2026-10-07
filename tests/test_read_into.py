import multiprocessing as mp
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import numpy as np
import pytest

from sharedbox import (
    BoxClosedError,
    DType,
    LockTimeoutError,
    Shape,
    SharedBox,
    SupportsDLPack,
)


@dataclass
class Tile:
    origin: tuple[int, int]
    pixels: Annotated[np.ndarray, Shape(2, 2), DType("int16")]


class Board(SharedBox):
    tile: Tile


class Camera(SharedBox, lock_timeout=0.2):
    image: Annotated[np.ndarray, Shape(512, 512), DType("float32")] = np.zeros(
        (512, 512), np.float32
    )
    small: Annotated[np.ndarray, Shape(4, 3), DType("int16")] = np.zeros(
        (4, 3), np.int16
    )
    raw: Annotated[SupportsDLPack, Shape(2), DType("uint8")] = np.zeros(2, np.uint8)
    count: int = 0


def write_in_child(box: Camera) -> None:
    box.update(
        image=np.full((512, 512), 2.5, np.float32),
        small=np.arange(12, dtype=np.int16).reshape(4, 3),
        raw=np.array([7, 9], np.uint8),
    )
    box.close()


def read_only() -> np.ndarray:
    out = np.empty((4, 3), np.int16)
    out.flags.writeable = False
    return out


def test_read_into_fills_the_given_arrays_with_another_process_write(
    unique_name: str,
) -> None:
    """Fill and return the caller's arrays with another process's write, for a 1 MiB, a small and a SupportsDLPack field."""
    with Camera.create(unique_name) as box:
        child = mp.get_context("spawn").Process(target=write_in_child, args=(box,))
        child.start()
        child.join(60)
        assert child.exitcode == 0
        image = np.empty((512, 512), np.float32)
        small = np.empty((4, 3), np.int16)
        raw = np.empty(2, np.uint8)
        assert box.read_into("image", image) is image
        assert box.read_into("small", small) is small
        assert box.read_into("raw", raw) is raw
        assert (image == 2.5).all()
        assert np.array_equal(small, np.arange(12).reshape(4, 3))
        assert np.array_equal(raw, [7, 9])
    Camera.unlink(unique_name)


@pytest.mark.parametrize(
    ("make", "error", "match"),
    [
        (lambda: np.empty((3, 4), np.int16), ValueError, r"shape \(3, 4\)"),
        (lambda: np.empty((4, 3), np.int32), ValueError, "dtype int32"),
        (lambda: np.empty((4, 6), np.int16)[:, ::2], TypeError, "C-contiguous"),
        (read_only, TypeError, "writable"),
        (lambda: [[0] * 3] * 4, TypeError, "array"),
    ],
)
def test_read_into_refuses_an_out_that_does_not_fit(
    unique_name: str,
    make: Callable[[], object],
    error: type[Exception],
    match: str,
) -> None:
    """Raise ValueError for another dtype or shape and TypeError for a strided, read-only or non-array out, naming the field."""
    with Camera.create(unique_name) as box:
        with pytest.raises(error, match=match) as raised:
            box.read_into("small", make())
        assert "Camera.small" in str(raised.value)
    Camera.unlink(unique_name)


@pytest.mark.parametrize(
    ("field", "error"), [("count", TypeError), ("nope", ValueError)]
)
def test_read_into_refuses_a_field_that_is_not_an_array(
    unique_name: str, field: str, error: type[Exception]
) -> None:
    """Raise TypeError for an int field and ValueError for a name that is not a field."""
    with Camera.create(unique_name) as box, pytest.raises(error):
        box.read_into(field, np.empty(1, np.int64))
    Camera.unlink(unique_name)


def test_read_into_refuses_a_record_holding_an_array(unique_name: str) -> None:
    """Raise TypeError for a record field, though one of its members is an array."""
    tile = Tile((1, 2), np.zeros((2, 2), np.int16))
    with (
        Board.create(unique_name, tile) as box,
        pytest.raises(TypeError, match="Board.tile"),
    ):
        box.read_into("tile", np.empty((2, 2), np.int16))
    Board.unlink(unique_name)


def test_read_into_raises_on_a_closed_box(unique_name: str) -> None:
    """Raise BoxClosedError after close()."""
    box = Camera.create(unique_name)
    box.close()
    with pytest.raises(BoxClosedError):
        box.read_into("small", np.empty((4, 3), np.int16))
    Camera.unlink(unique_name)


def test_read_into_times_out_while_a_writer_holds_the_lock(unique_name: str) -> None:
    """Raise LockTimeoutError when the write lock stays held past the lock timeout."""
    with Camera.create(unique_name) as box:
        box._segment._hold_write_lock()
        try:
            with pytest.raises(LockTimeoutError):
                box.read_into("small", np.empty((4, 3), np.int16))
        finally:
            box._segment._release_held_lock()
    Camera.unlink(unique_name)


def test_a_field_named_read_into_is_refused() -> None:
    """Refuse a subclass field named read_into, as other method names are."""
    with pytest.raises(TypeError, match="read_into clash with SharedBox methods"):
        type("Clash", (SharedBox,), {"__annotations__": {"read_into": int}})
