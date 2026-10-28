from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict, TypeVar, assert_type

from sharedbox import (
    Capacity,
    SharedBox,
    SharedReceiveStream,
    SharedSendStream,
    SharedStream,
)


@dataclass(frozen=True)
class Item:
    value: int


class Info(TypedDict):
    unit: str



class Motor(SharedBox[Info]):
    speed: int


class Plain(SharedBox):
    speed: int


def check_box_metadata(motor: Motor, plain: Plain) -> None:
    assert_type(motor.metadata, Info | None)
    assert_type(motor.metadata_version_of(1), Info)
    assert_type(plain.metadata, Mapping[str, Any] | None)
    assert_type(plain.metadata_version_of(1), Mapping[str, Any])


def check_default(stream: SharedStream[Item]) -> None:
    assert_type(stream, SharedStream[Item, Mapping[str, Any]])
    assert_type(stream.sender(), SharedSendStream[Item, Mapping[str, Any]])
    assert_type(stream.reader(), SharedReceiveStream[Item, Mapping[str, Any]])


def check_given(stream: SharedStream[Item, Info]) -> None:
    assert_type(stream.sender(), SharedSendStream[Item, Info])
    assert_type(stream.reader(), SharedReceiveStream[Item, Info])


def check_inference() -> None:
    assert_type(
        SharedStream.create(Item, capacity=2, metadata_type=Info),
        SharedStream[Item, Info],
    )
    assert_type(
        SharedStream.attach(Item, "name", metadata_type=Info),
        SharedStream[Item, Info],
    )
    assert_type(
        SharedStream.create(Item, capacity=2),
        SharedStream[Item, Mapping[str, Any]],
    )


B = TypeVar("B", bound=SharedBox)


def takes_box(box: SharedBox) -> None: ...
def takes_class(cls: type[SharedBox]) -> None: ...
def takes_stream(stream: SharedStream[Item]) -> None: ...
def takes_class_var(cls: type[B]) -> type[B]:
    return cls


def check_covariance(motor: Motor, stream: SharedStream[Item, Info]) -> None:
    takes_box(motor)
    takes_class(Motor)
    takes_stream(stream)
    assert_type(takes_class_var(Motor), type[Motor])


def check_bound(box: SharedBox[int], stream: SharedStream[Item, int]) -> None:  # type: ignore[type-var]
    pass


def check_stream_metadata(
    stream: SharedStream[Item], typed: SharedStream[Item, Info]
) -> None:
    assert_type(stream.metadata, Mapping[str, Any] | None)
    assert_type(stream.metadata_version_of(1), Mapping[str, Any])
    assert_type(stream.reader().metadata, Mapping[str, Any] | None)
    assert_type(typed.metadata, Info | None)
    assert_type(typed.metadata_version_of(1), Info)
    assert_type(typed.reader().metadata, Info | None)
    assert_type(
        SharedStream.create(Item, capacity=2, metadata={"unit": "mm"}),
        SharedStream[Item, Mapping[str, Any]],
    )


def check_annotated_item() -> None:
    assert_type(
        SharedStream.create(
            Annotated[list[int], Capacity(4)], capacity=2, metadata_type=Info
        ),
        SharedStream[Any, Info],
    )
    assert_type(
        SharedStream.attach(
            Annotated[list[int], Capacity(4)], "name", metadata_type=Info
        ),
        SharedStream[Any, Info],
    )
