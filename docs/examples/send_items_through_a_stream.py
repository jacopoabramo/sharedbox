"""The script of the guide "How to send items through a stream"."""

# --8<-- [start:types]
import multiprocessing as mp
from dataclasses import dataclass
from typing import Annotated

import numpy as np

from sharedbox import DType, Shape, SharedStream


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


# --8<-- [start:main]
def main() -> None:
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


if __name__ == "__main__":
    main()
# --8<-- [end:main]
