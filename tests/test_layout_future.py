from __future__ import annotations

from dataclasses import dataclass

from sharedbox import SharedBox


def test_a_box_class_in_a_function_names_a_type_of_that_function(
    unique_name: str,
) -> None:
    """Store a field annotated with a class defined in the same function, under string annotations."""

    @dataclass
    class Point:
        x: float = 0.0
        y: float = 0.0

    class Holder(SharedBox):
        point: Point

    with Holder.create(unique_name, Point(1.0, 2.0)) as box:
        assert box.point == Point(1.0, 2.0)
