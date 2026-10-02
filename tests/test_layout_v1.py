import queue
from typing import Annotated

from sharedbox import Capacity, SharedBox
from sharedbox._native import Segment


class Point(SharedBox):
    x: int = 0
    y: float = 0.0
    label: Annotated[str, Capacity(8)] = ""


def create_v1(name: str) -> Segment:
    layout = Point.__layout__
    return Segment._create_layout_1(
        name,
        [spec.native for spec in layout.fields],
        [spec.label for spec in layout.fields],
        layout.record_size,
        layout.schema_hash,
        1.0,
        [(0, 3), (2, "old")],
    )


def test_a_box_made_with_layout_1_works_through_the_python_api(
    unique_name: str,
) -> None:
    """Check that a box with the 0.3 layout attaches and supports reads, update, snapshot, events and its capsule."""
    owner = create_v1(unique_name)
    seen: queue.Queue[tuple[int, int]] = queue.Queue()
    with Point.attach(unique_name) as box:
        assert box.snapshot() == {"x": 3, "y": 0.0, "label": "old"}
        box.events.x.connect(lambda new, old: seen.put((new, old)))
        box.update(x=4, label="new")
        assert seen.get(timeout=5) == (4, 3)
        assert box._segment.layout_version == (1, 0)
        box.__sharedbox_box__(max_version=(1, 0))
    owner.close()
    Point.unlink(unique_name)
