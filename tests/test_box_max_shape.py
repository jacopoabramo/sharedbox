import multiprocessing as mp
import queue
import struct
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from multiprocessing.synchronize import Event
from typing import Annotated, Any

import numpy as np
import numpy.typing as npt
import pytest
from forged_open import raw_bytes

from sharedbox import Capacity, MaxShape, Shape, SharedBox, field
from sharedbox._native import Segment

Image = Annotated[npt.NDArray[np.uint32], MaxShape((4, 3))]


class Frame(SharedBox, lock_timeout=0.5):
    image: Image = np.zeros((2, 3), np.uint32)
    count: int = 0


class Fixed(SharedBox):
    image: Annotated[npt.NDArray[np.uint32], Shape((2, 3))] = np.zeros(
        (2, 3), np.uint32
    )


@dataclass
class Pair:
    image: Image
    count: int


class Shot(SharedBox, lock_timeout=0.5):
    pair: Pair


def grid(*shape: int) -> npt.NDArray[np.uint32]:
    return np.arange(int(np.prod(shape)), dtype=np.uint32).reshape(shape)


def collect(values: Iterable[Any]) -> "queue.Queue[Any]":
    out: queue.Queue[Any] = queue.Queue()

    def consume() -> None:
        for value in values:
            out.put(value)

    threading.Thread(target=consume, daemon=True).start()
    return out


def write_shapes(name: str, seen: Event) -> None:
    box = Frame.attach(name)
    with box.writing("image", shape=(0, 3)):
        pass
    seen.wait(30)
    box.image = grid(4, 3)
    box.close()


def read_from_thread(read: Any) -> list[BaseException]:
    errors: list[BaseException] = []

    def target() -> None:
        try:
            read()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()
    return errors


def test_a_max_shape_field_keeps_each_shape_across_processes(
    unique_name: str,
) -> None:
    """Read and watch the shapes another process writes: empty, then the maximum."""
    with Frame.create(unique_name, grid(2, 3)) as box:
        assert box.image.shape == (2, 3)
        values = collect(box.watch("image"))
        seen = mp.get_context("spawn").Event()
        child = mp.get_context("spawn").Process(
            target=write_shapes, args=(unique_name, seen)
        )
        child.start()
        try:
            first = values.get(timeout=30)
            assert first.shape == (0, 3)
            assert box.image.shape == (0, 3)
            assert box.snapshot()["image"].shape == (0, 3)
            seen.set()
            second = values.get(timeout=30)
            assert np.array_equal(second, grid(4, 3))
            assert np.array_equal(box.image, grid(4, 3))
            assert np.array_equal(box.snapshot()["image"], grid(4, 3))
        finally:
            seen.set()
            child.join(60)
        assert child.exitcode == 0
    Frame.unlink(unique_name)


def test_writing_with_a_shape_is_one_write_a_watcher_sees(unique_name: str) -> None:
    """Give a watcher the new shape and the filled data from one writing block."""
    with Frame.create(unique_name) as box:
        values = collect(box.watch("image"))
        with box.writing("image", shape=(1, 3)) as view:
            view[:] = 7
        got = values.get(timeout=30)
        assert got.shape == (1, 3)
        assert (got == 7).all()


def test_writing_without_a_shape_views_the_stored_shape(unique_name: str) -> None:
    """Yield a view of the stored shape holding the stored values."""
    with Frame.create(unique_name, grid(2, 3)) as box, box.writing("image") as view:
        assert view.shape == (2, 3)
        assert np.array_equal(view, grid(2, 3))


def test_read_into_refuses_a_max_shape_field(unique_name: str) -> None:
    """Raise TypeError when read_into is given a MaxShape field."""
    with Frame.create(unique_name) as box, pytest.raises(TypeError):
        box.read_into("image", np.zeros((4, 3), np.uint32))


def test_a_record_field_holds_a_max_shape_member(unique_name: str) -> None:
    """Write and read a record's MaxShape member at two shapes."""
    with Shot.create(unique_name, Pair(grid(2, 3), 1)) as box:
        assert np.array_equal(box.pair.image, grid(2, 3))
        box.pair = Pair(grid(0, 3), 2)
        assert box.pair.image.shape == (0, 3)
        assert box.pair.count == 2


@pytest.mark.parametrize("shape", [(5, 3), (4,), (1, 1, 1)])
def test_writing_refuses_a_bad_shape_before_the_lock(
    unique_name: str, shape: tuple[int, ...]
) -> None:
    """Raise ValueError for a shape beyond the maximum or of other dimensions, taking no lock."""
    with Frame.create(unique_name) as box:
        with (
            pytest.raises(ValueError, match="MaxShape"),
            box.writing("image", shape=shape),
        ):
            pass
        assert read_from_thread(lambda: box.image) == []


def test_shape_on_a_fixed_shape_field_is_refused(unique_name: str) -> None:
    """Raise TypeError when writing is given a shape for a field with a fixed Shape."""
    with (
        Fixed.create(unique_name) as box,
        pytest.raises(TypeError),
        box.writing("image", shape=(2, 3)),
    ):
        pass


@pytest.mark.parametrize("shape", [(5, 3), (12,)])
def test_a_write_that_does_not_fit_is_refused(
    unique_name: str, shape: tuple[int, ...]
) -> None:
    """Raise ValueError for an array beyond the maximum and keep the stored value."""
    with Frame.create(unique_name, grid(2, 3)) as box:
        with pytest.raises(ValueError, match="at most MaxShape"):
            box.image = grid(*shape)
        assert np.array_equal(box.image, grid(2, 3))


@dataclass
class Plain:
    image: Image


def define_optional() -> None:
    class Held(SharedBox):
        image: Image | None = None


def define_union() -> None:
    class Held(SharedBox):
        image: Image | int = 0


def define_list_element() -> None:
    class Held(SharedBox):
        blocks: Annotated[list[Plain], Capacity(2)]


def define_with_shape() -> None:
    class Held(SharedBox):
        image: Annotated[npt.NDArray[np.uint32], Shape((4, 3)), MaxShape((4, 3))]


def define_with_option() -> None:
    class Held(SharedBox):
        image: Image = field(shape=(4, 3))


@pytest.mark.parametrize(
    "define",
    [
        define_optional,
        define_union,
        define_list_element,
        define_with_shape,
        define_with_option,
    ],
)
def test_max_shape_is_refused_in_a_box_where_a_stream_refuses_it(define: Any) -> None:
    """Raise TypeError at class definition for MaxShape under an optional, union or list, or with a Shape."""
    with pytest.raises(TypeError, match="MaxShape|union member"):
        define()


@pytest.mark.parametrize("shape", [(1, 3), (0, 3)])
def test_a_default_array_of_a_smaller_shape_is_accepted(
    unique_name: str, shape: tuple[int, ...]
) -> None:
    """Build a class whose default array is smaller than the maximum and read that shape back."""

    class Small(SharedBox):
        image: Image = np.zeros(shape, np.uint32)

    with Small.create(unique_name) as box:
        assert box.image.shape == shape


def forge_first_dimension(
    name: str, box_class: type[SharedBox], field_name: str
) -> None:
    layout = box_class.__layout__
    spec = next(spec for spec in layout.fields if spec.name == field_name)
    with raw_bytes(name) as view:
        (record,) = struct.unpack_from("<I", view, 96)
        struct.pack_into("<Q", view, record + spec.offset, 5)


def test_a_stored_shape_beyond_the_maximum_is_refused_on_every_read(
    unique_name: str,
) -> None:
    """Raise ValueError for a forged shape on a read, a snapshot and writing, and release the lock."""
    with Frame.create(unique_name, grid(2, 3)) as box:
        forge_first_dimension(unique_name, Frame, "image")
        with pytest.raises(ValueError, match="is not one Python can hold"):
            _ = box.image
        with pytest.raises(ValueError, match="is not one Python can hold"):
            box.snapshot()
        with (
            pytest.raises(ValueError, match="is not one Python can hold"),
            box.writing("image"),
        ):
            pass
        assert read_from_thread(lambda: setattr(box, "count", 1)) == []
        assert box.count == 1


def test_a_forged_shape_in_a_record_member_is_refused(unique_name: str) -> None:
    """Raise ValueError when a record's MaxShape member holds a shape beyond the maximum."""
    with Shot.create(unique_name, Pair(grid(2, 3), 1)) as box:
        forge_first_dimension(unique_name, Shot, "pair")
        with pytest.raises(ValueError, match="is not one Python can hold"):
            _ = box.pair


def test_a_value_that_does_not_match_its_shape_line_is_refused(
    unique_name: str,
) -> None:
    """Raise ValueError naming the field for bytes that differ from their shape line."""
    layout = Frame.__layout__
    with Frame.create(unique_name) as box:
        segment = Segment.attach(
            unique_name, layout.names, layout.schema_hash, 1.0, layout.types
        )
        line = struct.pack("<8Q", 1, 3, 0, 0, 0, 0, 0, 0)
        with pytest.raises(ValueError, match="field 0 does not match its shape line"):
            segment._write([(0, line + bytes(8))])
        segment.close()
        assert box.image.shape == (2, 3)
