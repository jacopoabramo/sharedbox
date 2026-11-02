from collections.abc import MutableSequence
from typing import Annotated, Any, TypeVar, assert_type

from sharedbox import BoundedDict, BoundedList, Capacity, SharedBox

T = TypeVar("T")


class MyList(BoundedList[T]):
    pass


class Points(BoundedList[float]):
    __slots__ = ()


class Scan(SharedBox):
    points: Annotated[BoundedList[float], Capacity(8)]
    pts: Annotated[Points, Capacity(4)]


def reads(box: Scan) -> None:
    assert_type(box.points, BoundedList[float])
    assert_type(box.points[0], float)
    assert_type(box.points.copy(), BoundedList[float])
    assert_type(box.points + [1.0], BoundedList[float])
    assert_type(box.points * 2, BoundedList[float])
    assert_type(box.pts, Points)
    sequence: MutableSequence[float] = box.points
    del sequence


def builds() -> None:
    assert_type(BoundedList[float]([1.0], capacity=4), BoundedList[float])
    assert_type(Points([1.0], capacity=4), Points)
    assert_type(MyList[float]([1.0], capacity=4), MyList[float])
    assert_type(BoundedList(int, [1], capacity=4), BoundedList[int])
    assert_type(
        BoundedDict(str, float, {"a": 1.0}, capacity=8), BoundedDict[str, float]
    )
    assert_type(
        BoundedList(Annotated[str, Capacity(8)], ["a"], capacity=2), BoundedList[Any]
    )
    assert_type(MyList(int, [1], capacity=4), MyList[int])
