from collections.abc import Callable
from typing import Annotated, Any

import attrs
import numpy as np
import numpy.typing as npt
import pytest
from crossproc import snapshot_in_child

from sharedbox import Capacity, SharedBox


@attrs.define(frozen=True)
class Reading:
    _raw: int
    scale: float = attrs.field(default=1.0, converter=float)


class Meter(SharedBox):
    reading: Reading = Reading(0)


def test_an_attrs_class_reads_back_through_its_aliases(unique_name: str) -> None:
    """Check that an attrs class with a private attribute reads back equal, built through the attribute's alias, in another process."""
    with Meter.create(unique_name, Reading(5, 2)) as box:
        assert snapshot_in_child(box)["reading"] == Reading(5, 2.0)
    Meter.unlink(unique_name)


@attrs.frozen
class Mark:
    n: int


@attrs.define
class Note:
    n: int


@attrs.frozen
class MarkWithList:
    items: Annotated[list[int], Capacity(2)]


class Marks(SharedBox):
    mark: Mark
    note: Note
    mark_with_list: MarkWithList


STORED: dict[str, Any] = {
    "mark": Mark(1),
    "note": Note(1),
    "mark_with_list": MarkWithList([1]),
}


def test_a_frozen_attrs_class_is_reused_until_a_write(unique_name: str) -> None:
    """Check that two reads of a frozen attrs record return one object, and a read after a write returns the written value."""
    with Marks.create(unique_name, **STORED) as box:
        first = box.mark
        assert box.mark is first
        box.mark = Mark(2)
        assert box.mark == Mark(2)
    Marks.unlink(unique_name)


@pytest.mark.parametrize(
    ("name", "change"),
    [
        ("note", lambda v: setattr(v, "n", 2)),
        ("mark_with_list", lambda v: v.items.append(2)),
    ],
)
def test_an_attrs_record_that_can_change_is_never_shared(
    unique_name: str, name: str, change: Callable[[Any], object]
) -> None:
    """Check that changing an attrs record that can be changed, or a frozen one holding a list, leaves the next read a new object equal to the stored value."""
    with Marks.create(unique_name, **STORED) as box:
        first = getattr(box, name)
        change(first)
        second = getattr(box, name)
        assert second is not first
        assert second == STORED[name]
    Marks.unlink(unique_name)


@attrs.frozen
class Strip:
    pixels: npt.NDArray[np.uint8] = attrs.field(metadata={"shape": (4,)})


class Scanner(SharedBox):
    strip: Strip


def test_an_attrs_member_takes_its_array_sizes_from_metadata(unique_name: str) -> None:
    """Store an attrs member array whose sizes come from the attribute's metadata."""
    with Scanner.create(unique_name, Strip(np.arange(4, dtype=np.uint8))) as box:
        assert np.array_equal(box.strip.pixels, np.arange(4, dtype=np.uint8))
    Scanner.unlink(unique_name)
