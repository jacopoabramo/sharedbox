from concurrent.futures import Future
from dataclasses import dataclass
from typing import assert_type

import numpy as np

from sharedbox import (
    IterInto,
    Mode,
    OnFull,
    ReceiveEvents,
    SendSlot,
    SharedReceiveStream,
    SharedSendStream,
    SharedStream,
    Start,
)


@dataclass(frozen=True)
class Item:
    value: int


def check(stream: SharedStream[Item]) -> None:
    reader = stream.reader()
    assert_type(reader, SharedReceiveStream[Item])
    assert_type(reader.receive(), Item)
    assert_type(reader.events, ReceiveEvents)
    assert_type(reader.receive_nowait(), Item)
    for item in reader:
        assert_type(item, Item)
    assert_type(SharedStream.attach(Item, "name"), SharedStream[Item])
    assert_type(SharedStream.create(Item, capacity=2), SharedStream[Item])
    assert_type(reader.stream, SharedStream[Item])
    assert_type(stream.sender().stream, SharedStream[Item])


async def check_async(stream: SharedStream[Item]) -> None:
    reader = stream.reader()
    async for item in reader:
        assert_type(item, Item)
    assert_type(await anext(reader), Item)
    assert_type(reader.receive_future(), Future[Item])


async def check_iter_into(stream: SharedStream[np.ndarray]) -> None:
    async for frame in stream.reader().iter_into(np.empty(2)):
        assert_type(frame, np.ndarray)


@dataclass(frozen=True)
class Shot:
    index: int
    image: np.ndarray


def check_writing(sender: SharedSendStream[Shot]) -> None:
    with sender.writing(Shot(0, np.empty(0))) as slot:
        assert_type(slot, SendSlot[Shot])
        assert_type(slot.item, Shot)
        assert_type(slot.item.image, np.ndarray)


async def check_entered(
    sender: SharedSendStream[Shot], reader: SharedReceiveStream[Item]
) -> None:
    with sender as entered_sender, reader as entered_reader:
        assert_type(entered_sender, SharedSendStream[Shot])
        assert_type(entered_reader, SharedReceiveStream[Item])
    async with sender as awaited_sender, reader as awaited_reader:
        assert_type(awaited_sender, SharedSendStream[Shot])
        assert_type(awaited_reader, SharedReceiveStream[Item])


async def check_anext(reader: SharedReceiveStream[Item], out: Item) -> None:
    assert_type(await anext(reader), Item)
    assert_type(await reader.__anext__(), Item)
    assert_type(await anext(reader.iter_into(out)), Item)
    async for item in reader:
        assert_type(item, Item)


def check_unlink(stream: SharedStream[Item]) -> None:
    assert_type(stream.unlink(), None)
    assert_type(SharedStream.unlink("name"), None)


def check_exported_names(stream: SharedStream[Item], out: Item) -> None:
    mode: Mode = "lossy"
    start: Start = "oldest"
    on_full: OnFull = "release"
    assert_type(stream.reader(mode, start).iter_into(out), IterInto[Item])
    assert_type(stream.sender(on_full), SharedSendStream[Item])
