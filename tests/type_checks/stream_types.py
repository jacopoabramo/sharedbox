from concurrent.futures import Future
from dataclasses import dataclass
from typing import assert_type

import numpy as np

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


async def check_async(stream: SharedStream[Item]) -> None:
    reader = stream.reader()
    async for item in reader:
        assert_type(item, Item)
    assert_type(await anext(reader), Item)
    assert_type(reader.receive_future(), Future[Item])


async def check_iter_into(stream: SharedStream[np.ndarray]) -> None:
    async for frame in stream.reader().iter_into(np.empty(2)):
        assert_type(frame, np.ndarray)
