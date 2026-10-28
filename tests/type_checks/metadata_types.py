from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, assert_type

from sharedbox import (
    SharedBox,
    SharedReceiveStream,
    SharedSendStream,
    SharedStream,
)


@dataclass(frozen=True)
class Item:
    value: int


@dataclass(frozen=True)
class Info:
    unit: str


class Motor(SharedBox[Info]):
    speed: int


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
