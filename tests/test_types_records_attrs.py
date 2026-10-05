import attrs
from crossproc import snapshot_in_child

from sharedbox import SharedBox


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
