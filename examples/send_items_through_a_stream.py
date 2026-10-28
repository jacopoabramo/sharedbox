"""The script of the guide "How to send items through a stream"."""

# --8<-- [start:types]
import asyncio
import multiprocessing as mp
from dataclasses import dataclass, replace
from typing import Annotated

import numpy as np

from sharedbox import (
    Capacity,
    DType,
    EndOfStream,
    MaxShape,
    ReaderReleased,
    Shape,
    SharedReceiveStream,
    SharedSendStream,
    SharedStream,
)


@dataclass(frozen=True)
class Letter:
    index: int
    recipient: Annotated[str, Capacity(32)]
    scan: Annotated[np.ndarray, Shape((32, 32)), DType("uint8")]


# --8<-- [end:types]


# --8<-- [start:reader]
def register(stream: SharedStream[Letter], ready: "mp.synchronize.Event") -> None:
    reader = stream.reader(mode="lossless")
    ready.set()
    scan = np.empty((32, 32), np.uint8)
    for letter in reader.iter_into({"scan": scan}):
        print(letter.index, letter.recipient, int(letter.scan[0, 0]))


# --8<-- [end:reader]


# --8<-- [start:tasks]
async def produce(sender: SharedSendStream[Letter]) -> None:
    async with sender:
        for i in range(3):
            await sender.asend(Letter(i, "Ada", np.full((32, 32), i, np.uint8)))


async def consume(reader: SharedReceiveStream[Letter]) -> None:
    async with reader:
        async for letter in reader:
            print(letter.index)


async def run_tasks(stream: SharedStream[Letter]) -> None:
    async with asyncio.TaskGroup() as group:
        group.create_task(consume(stream.reader()))
        group.create_task(produce(stream.sender()))


# --8<-- [end:tasks]


# --8<-- [start:first]
async def print_from_any(readers: list[SharedReceiveStream[Letter]]) -> None:
    pending = {asyncio.wrap_future(r.receive_future()): r for r in readers}
    while pending:
        done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for future in done:
            reader = pending.pop(future)
            try:
                letter = future.result()
            except EndOfStream:
                continue
            print(letter.index)
            pending[asyncio.wrap_future(reader.receive_future())] = reader


# --8<-- [end:first]


# --8<-- [start:writing]
def send_filled(sender: SharedSendStream[Letter], count: int) -> None:
    blank = Letter(0, "Ada", np.empty((0, 0), np.uint8))
    for i in range(count):
        with sender.writing(blank) as slot:
            slot.item.scan.fill(i)
            slot.send(replace(slot.item, index=i))


# --8<-- [end:writing]


# --8<-- [start:shapes]
@dataclass(frozen=True)
class Sack:
    index: int
    weights: Annotated[np.ndarray, MaxShape((64,)), DType("float32")]


def send_sacks(sender: SharedSendStream[Sack]) -> None:
    sender.send(Sack(0, np.array([1.5, 0.25, 4.0], np.float32)))
    template = Sack(1, np.empty(64, np.float32))
    with sender.writing(template) as slot:
        weights = slot.item.weights
        weights[:5] = 2.5
        slot.send(replace(slot.item, weights=weights[:5]))


def read_sacks(reader: SharedReceiveStream[Sack]) -> None:
    weights = np.empty(64, np.float32)
    for sack in reader.iter_into({"weights": weights}):
        print(sack.index, sack.weights.shape)


# --8<-- [end:shapes]


# --8<-- [start:release]
def sort_without_stalling(sender: SharedSendStream[Letter], count: int) -> None:
    for i in range(count):
        sender.send(Letter(i, "Ada", np.full((32, 32), i, np.uint8)), timeout=0.1)


def file_letters(reader: SharedReceiveStream[Letter]) -> None:
    while True:
        try:
            for letter in reader:
                print("filed", letter.index)
            return
        except ReaderReleased as released:
            print("missed", released.missed)


# --8<-- [end:release]


# --8<-- [start:main]
if __name__ == "__main__":
    context = mp.get_context("spawn")
    ready = context.Event()
    with SharedStream.create(Letter, capacity=8) as stream:
        desk = context.Process(target=register, args=(stream, ready))
        desk.start()
        ready.wait(60)
        with stream.sender() as sender:
            for i in range(3):
                sender.send(Letter(i, "Ada", np.full((32, 32), i, np.uint8)))
        desk.join(60)
    SharedStream.unlink(stream.name)
# --8<-- [end:main]


if __name__ == "__main__":
    with SharedStream.create(Letter, "example-sorting:tasks", capacity=8) as stream:
        asyncio.run(run_tasks(stream))
    SharedStream.unlink("example-sorting:tasks")
    with SharedStream.create(Letter, "example-sorting:merged", capacity=8) as stream:
        readers = [stream.reader(), stream.reader()]
        with stream.sender() as sender:
            for i in range(2):
                sender.send(Letter(i, "Ada", np.full((32, 32), i, np.uint8)))
        asyncio.run(print_from_any(readers))
    SharedStream.unlink("example-sorting:merged")
    with SharedStream.create(Letter, "example-sorting:filled", capacity=8) as stream:
        reader = stream.reader()
        with stream.sender() as sender:
            send_filled(sender, 2)
        for letter in reader:
            print(letter.index, int(letter.scan[0, 0]))
    SharedStream.unlink("example-sorting:filled")
    with SharedStream.create(Sack, "example-sorting:sacks", capacity=4) as sacks:
        sack_reader = sacks.reader()
        with sacks.sender() as sack_sender:
            send_sacks(sack_sender)
        read_sacks(sack_reader)
    SharedStream.unlink("example-sorting:sacks")
    with SharedStream.create(Letter, "example-sorting:release", capacity=4) as stream:
        reader = stream.reader()
        with stream.sender(on_full="release") as sender:
            sort_without_stalling(sender, 6)
        file_letters(reader)
    SharedStream.unlink("example-sorting:release")
