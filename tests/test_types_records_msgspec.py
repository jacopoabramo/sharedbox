from collections.abc import Callable
from typing import Annotated, Any

import msgspec
import pytest
from crossproc import snapshot_in_child

from sharedbox import Capacity, SharedBox


class Position(msgspec.Struct, frozen=True):
    x: int
    y: int = 0


class Robot(SharedBox):
    position: Position = Position(0)


def test_a_struct_reads_back_equal(unique_name: str) -> None:
    """Check that a msgspec Struct reads back equal and of its class in another process."""
    with Robot.create(unique_name, Position(3, 4)) as box:
        seen = snapshot_in_child(box)["position"]
        assert seen == Position(3, 4)
        assert type(seen) is Position
    Robot.unlink(unique_name)


class Fleet(SharedBox):
    seen: Annotated[set[Position], Capacity(4)] = set()  # noqa: RUF012


def test_frozen_structs_can_be_set_elements(unique_name: str) -> None:
    """Check that a set of frozen Structs reads back equal."""
    with Fleet.create(unique_name, {Position(1), Position(2, 3)}) as box:
        assert box.seen == {Position(1), Position(2, 3)}
    Fleet.unlink(unique_name)


class Spot(msgspec.Struct, frozen=True):
    n: int


class LooseSpot(msgspec.Struct):
    n: int


class SpotWithList(msgspec.Struct, frozen=True):
    items: Annotated[list[int], Capacity(2)]


class Spots(SharedBox):
    spot: Spot
    loose_spot: LooseSpot
    spot_with_list: SpotWithList


STORED: dict[str, Any] = {
    "spot": Spot(1),
    "loose_spot": LooseSpot(1),
    "spot_with_list": SpotWithList([1]),
}


def test_a_frozen_struct_is_reused_until_a_write(unique_name: str) -> None:
    """Check that two reads of a frozen Struct return one object, and a read after a write returns the written value."""
    with Spots.create(unique_name, **STORED) as box:
        first = box.spot
        assert box.spot is first
        box.spot = Spot(2)
        assert box.spot == Spot(2)
    Spots.unlink(unique_name)


@pytest.mark.parametrize(
    ("name", "change"),
    [
        ("loose_spot", lambda v: setattr(v, "n", 2)),
        ("spot_with_list", lambda v: v.items.append(2)),
    ],
)
def test_a_struct_that_can_change_is_never_shared(
    unique_name: str, name: str, change: Callable[[Any], object]
) -> None:
    """Check that changing a Struct that can be changed, or a frozen one holding a list, leaves the next read a new object equal to the stored value."""
    with Spots.create(unique_name, **STORED) as box:
        first = getattr(box, name)
        change(first)
        second = getattr(box, name)
        assert second is not first
        assert second == STORED[name]
    Spots.unlink(unique_name)
