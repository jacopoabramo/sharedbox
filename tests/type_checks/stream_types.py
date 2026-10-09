from dataclasses import dataclass
from typing import assert_type

from sharedbox import SharedStream, StreamReader


@dataclass(frozen=True)
class Item:
    value: int


def check(stream: SharedStream[Item]) -> None:
    reader = stream.reader()
    assert_type(reader, StreamReader[Item])
    assert_type(reader.receive(), Item)
    assert_type(reader.receive_nowait(), Item)
    for item in reader:
        assert_type(item, Item)
    assert_type(SharedStream.attach(Item, "name"), SharedStream[Item])
