import gc
import threading
from typing import Annotated, Any

import numpy as np
import pytest

from sharedbox import (
    BoxClosedError,
    DType,
    LockTimeoutError,
    Shape,
    SharedBox,
    SupportsDLPack,
    register_array_type,
)
from sharedbox._arrays import CONVERTERS, key


class Camera(SharedBox, lock_timeout=0.2):
    image: Annotated[np.ndarray, Shape(512, 512), DType("float32")] = np.zeros(
        (512, 512), np.float32
    )
    small: Annotated[np.ndarray, Shape(4, 3), DType("int16")] = np.zeros(
        (4, 3), np.int16
    )
    raw: Annotated[SupportsDLPack, Shape(2), DType("uint8")] = np.zeros(2, np.uint8)
    count: int = 0


class Refusing:
    """An array type whose converter always raises."""


def refuse(array: object) -> Any:
    raise RuntimeError("the converter refused")


def test_writing_fills_the_field_in_place_and_wakes_a_watcher(unique_name: str) -> None:
    """Show the filled field to another handle, count one write and wake a watcher."""
    with Camera.create(unique_name) as box, Camera.attach(unique_name) as other:
        values = iter(other.watch("small"))
        watchdog = threading.Timer(10.0, other.close)
        watchdog.start()
        with box.writing("small") as small:
            assert isinstance(small, np.ndarray)
            assert small.shape == (4, 3) and small.dtype == np.int16
            small[...] = np.arange(12, dtype=np.int16).reshape(4, 3)
        seen = next(values, None)
        watchdog.cancel()
        assert seen is not None
        assert np.array_equal(seen, np.arange(12).reshape(4, 3))
        assert np.array_equal(other.small, np.arange(12).reshape(4, 3))
    Camera.unlink(unique_name)


def test_writing_finishes_the_write_when_the_block_raises(unique_name: str) -> None:
    """Propagate the block's exception and keep what it wrote, counted as a write."""
    with Camera.create(unique_name) as box:
        values = iter(box.watch("small"))
        watchdog = threading.Timer(10.0, box.close)
        watchdog.start()
        with (
            pytest.raises(RuntimeError, match="halfway"),
            box.writing("small") as small,
        ):
            small[0, 0] = 5
            raise RuntimeError("halfway")
        seen = next(values, None)
        watchdog.cancel()
        assert seen is not None and seen[0, 0] == 5
        assert box.small[0, 0] == 5
    Camera.unlink(unique_name)


def test_a_view_outlives_close_without_crashing(unique_name: str) -> None:
    """Keep the view usable after close(), since it holds its own mapping."""
    box = Camera.create(unique_name)
    with box.writing("small") as small:
        small[...] = 1
    box.close()
    del box
    gc.collect()
    small[0, 0] = 7
    Camera.unlink(unique_name)


def test_close_frees_the_segment_of_a_box_that_used_writing(unique_name: str) -> None:
    """Free the segment at close(), so the name can be created again while the closed box is still referenced."""
    box = Camera.create(unique_name)
    with box.writing("small"):
        pass
    box.close()
    Camera.unlink(unique_name)
    again = Camera.create(unique_name)
    again.close()
    Camera.unlink(unique_name)


def test_a_dlpack_field_is_written_through_from_dlpack(unique_name: str) -> None:
    """Write a SupportsDLPack field through numpy.from_dlpack of the view."""
    with Camera.create(unique_name) as box:
        with box.writing("raw") as raw:
            np.from_dlpack(raw)[...] = [7, 9]
        assert np.array_equal(np.from_dlpack(box.raw), [7, 9])
    Camera.unlink(unique_name)


@pytest.mark.parametrize(
    ("field", "error"), [("count", TypeError), ("nope", ValueError)]
)
def test_writing_refuses_a_field_that_is_not_an_array(
    unique_name: str, field: str, error: type[Exception]
) -> None:
    """Raise TypeError for an int field and ValueError for a name that is not a field."""
    with Camera.create(unique_name) as box, pytest.raises(error), box.writing(field):
        pass
    Camera.unlink(unique_name)


def test_writing_raises_on_a_closed_box(unique_name: str) -> None:
    """Raise BoxClosedError after close()."""
    box = Camera.create(unique_name)
    box.close()
    with pytest.raises(BoxClosedError), box.writing("small"):
        pass
    Camera.unlink(unique_name)


def test_reads_and_nested_writing_time_out_while_a_block_holds_the_lock(
    unique_name: str,
) -> None:
    """Raise LockTimeoutError for a read from another handle and for writing inside the block."""
    with Camera.create(unique_name) as box, Camera.attach(unique_name) as other:
        with box.writing("small"):
            with pytest.raises(LockTimeoutError):
                other.small  # noqa: B018
            with pytest.raises(LockTimeoutError), box.writing("image"):
                pass
        assert not other.small.any()
    Camera.unlink(unique_name)


def test_a_converter_that_raises_releases_the_lock(unique_name: str) -> None:
    """Release the write lock when the field's converter raises while the view is built."""
    register_array_type(Refusing, refuse)
    try:
        odd: Any = type(
            "Odd",
            (SharedBox,),
            {
                "__annotations__": {
                    "data": Annotated[Refusing, Shape(3), DType("int32")]
                },
                "__module__": __name__,
                "__lock_timeout__": 0.2,
            },
        )
        with odd.create(unique_name, np.zeros(3, np.int32)) as box:
            with pytest.raises(RuntimeError, match="refused"), box.writing("data"):
                pass
            box.data = np.ones(3, np.int32)
        odd.unlink(unique_name)
    finally:
        del CONVERTERS[key(Refusing)]


def test_a_field_named_writing_is_refused() -> None:
    """Refuse a subclass field named writing, as other method names are."""
    with pytest.raises(TypeError, match="writing clash with SharedBox methods"):
        type("Clash", (SharedBox,), {"__annotations__": {"writing": int}})
