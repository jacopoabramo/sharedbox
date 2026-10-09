"""The script of the guide "How to send items through a stream"."""

# --8<-- [start:types]
import asyncio
import multiprocessing as mp
from dataclasses import dataclass
from typing import Annotated

import numpy as np

from sharedbox import (
    DType,
    EndOfStream,
    Shape,
    SharedStream,
    StreamReader,
    StreamSender,
)


@dataclass(frozen=True)
class Batch:
    index: int
    values: Annotated[np.ndarray, Shape(1024), DType("float64")]


# --8<-- [end:types]


# --8<-- [start:reader]
def process_batches(name: str, ready: "mp.synchronize.Event") -> None:
    stream = SharedStream.attach(Batch, name)
    reader = stream.reader(mode="lossless")
    ready.set()
    values = np.empty(1024, np.float64)
    for batch in reader.iter_into({"values": values}):
        print(batch.index, float(batch.values[0]))


# --8<-- [end:reader]


# --8<-- [start:tasks]
async def produce(sender: StreamSender[Batch]) -> None:
    async with sender:
        for i in range(3):
            await sender.asend(Batch(i, np.full(1024, float(i))))


async def consume(reader: StreamReader[Batch]) -> None:
    async with reader:
        async for batch in reader:
            print(batch.index)


async def run_tasks(stream: SharedStream[Batch]) -> None:
    async with asyncio.TaskGroup() as group:
        group.create_task(consume(stream.reader()))
        group.create_task(produce(stream.sender()))


# --8<-- [end:tasks]


# --8<-- [start:first]
async def print_from_any(readers: list[StreamReader[Batch]]) -> None:
    pending = {asyncio.wrap_future(r.receive_future()): r for r in readers}
    while pending:
        done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for future in done:
            reader = pending.pop(future)
            try:
                batch = future.result()
            except EndOfStream:
                continue
            print(batch.index)
            pending[asyncio.wrap_future(reader.receive_future())] = reader


# --8<-- [end:first]


# --8<-- [start:main]
if __name__ == "__main__":
    context = mp.get_context("spawn")
    ready = context.Event()
    with SharedStream.create(Batch, "example-pipeline:batches", capacity=8) as stream:
        child = context.Process(target=process_batches, args=(stream.name, ready))
        child.start()
        ready.wait(60)
        with stream.sender() as sender:
            for i in range(3):
                sender.send(Batch(i, np.full(1024, float(i))))
        child.join(60)
    SharedStream.unlink("example-pipeline:batches")
# --8<-- [end:main]


if __name__ == "__main__":
    with SharedStream.create(Batch, "example-pipeline:tasks", capacity=8) as stream:
        asyncio.run(run_tasks(stream))
    SharedStream.unlink("example-pipeline:tasks")
    with SharedStream.create(Batch, "example-pipeline:merged", capacity=8) as stream:
        readers = [stream.reader(), stream.reader()]
        with stream.sender() as sender:
            for i in range(2):
                sender.send(Batch(i, np.full(1024, float(i))))
        asyncio.run(print_from_any(readers))
    SharedStream.unlink("example-pipeline:merged")
