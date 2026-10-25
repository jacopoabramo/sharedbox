from collections.abc import MutableSequence
from typing import Annotated, assert_type

from sharedbox import BoundedList, Capacity, SharedBox


class Scan(SharedBox):
    points: Annotated[BoundedList[float], Capacity(8)]


def reads(box: Scan) -> None:
    assert_type(box.points, BoundedList[float])
    assert_type(box.points[0], float)
    assert_type(box.points.copy(), BoundedList[float])
    assert_type(box.points + [1.0], BoundedList[float])
    assert_type(box.points * 2, BoundedList[float])
    sequence: MutableSequence[float] = box.points
    del sequence


def builds() -> None:
    assert_type(BoundedList[float]([1.0], capacity=4), BoundedList[float])
