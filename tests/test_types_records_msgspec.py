import msgspec
from crossproc import snapshot_in_child

from sharedbox import SharedBox


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
