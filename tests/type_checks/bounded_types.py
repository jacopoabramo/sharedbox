from typing import Annotated, assert_type

from sharedbox import BoundedList, Capacity, SharedBox


class Scan(SharedBox):
    points: Annotated[BoundedList[float], Capacity(8)]


def reads(box: Scan) -> None:
    assert_type(box.points, BoundedList[float])
    assert_type(box.points[0], float)
