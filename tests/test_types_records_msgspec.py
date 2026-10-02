from typing import Annotated

import msgspec
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
