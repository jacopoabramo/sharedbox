import asyncio
import os
import threading
import traceback
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Barrier, Event
from typing import Annotated, Literal

import numpy as np

from sharedbox import DType, Shape, SharedStream

Mode = Literal["lossless", "lossy", "latest"]
SenderKind = Literal["send", "asend"]
ReaderKind = Literal["receive", "async", "signal"]


def send_ints(name: str, count: int) -> None:
    """Send the integers below `count` on the stream `name`, then close the sender."""
    stream = SharedStream.attach(int, name)
    sender = stream.sender()
    for i in range(count):
        sender.send(i)
    sender.close()
    stream.close()


def exit_while_delivering(name: str) -> None:
    """Connect a callback to a reader of the stream `name` and return, leaving delivery running."""
    stream = SharedStream.attach(int, name)
    reader = stream.reader()
    reader.events.received.connect(lambda item, position: None)
    stream.sender().send(1)


@dataclass(frozen=True)
class Frame:
    index: int
    image: Annotated[np.ndarray, Shape(64, 64), DType("uint16")]


def frame(i: int) -> Frame:
    return Frame(i, np.full((64, 64), i % 65536, np.uint16))


def finish(*children: BaseProcess) -> None:
    """Wait for the children, stop the ones that hang, and require exit code 0 from all."""
    for child in children:
        child.join(60)
    stuck = [child for child in children if child.is_alive()]
    for child in stuck:
        child.terminate()
        child.join(10)
    assert not stuck, "a child process did not finish within 60 s"
    assert [child.exitcode for child in children] == [0] * len(children)


def send_frames(name: str, count: int, barrier: "Barrier") -> None:
    """Send `count` frames once every party has reached the barrier, then close the sender."""
    stream = SharedStream.attach(Frame, name)
    barrier.wait(60)
    with stream.sender() as sender:
        for i in range(count):
            sender.send(frame(i), timeout=60)


def read_frames(
    name: str,
    mode: Mode,
    results: "Queue[tuple[str, list[int], int, int]]",
    barrier: "Barrier",
) -> None:
    """Receive frames into one array until the stream ends; report the indexes, misses and torn images."""
    stream = SharedStream.attach(Frame, name)
    reader = stream.reader(mode=mode, start="oldest")
    barrier.wait(60)
    image = np.empty((64, 64), np.uint16)
    seen: list[int] = []
    torn = 0
    for item in reader.iter_into({"image": image}):
        seen.append(item.index)
        torn += int(image.min() != image.max() or image[0, 0] != item.index % 65536)
    results.put((mode, seen, reader.missed, torn))


def hold_sender_then_die(name: str, ready: Event) -> None:
    """Hold the sender, send one frame, and exit without closing anything."""
    sender = SharedStream.attach(Frame, name).sender()
    sender.send(frame(0))
    ready.set()
    os._exit(0)


def read_then_die(name: str, ready: Event) -> None:
    """Open a lossless reader and exit without closing it."""
    SharedStream.attach(Frame, name).reader(mode="lossless")
    ready.set()
    os._exit(0)


def hold_sender_until(name: str, ready: Event, release: Event) -> None:
    """Hold the sender until `release`, then exit without closing it."""
    SharedStream.attach(int, name).sender()
    ready.set()
    release.wait(60)
    os._exit(0)


def send_ints_as(name: str, kind: SenderKind, count: int, ready: Event) -> None:
    """Send the integers below `count` with `send` or `asend` once `ready` is set, then close the sender."""
    stream = SharedStream.attach(int, name)
    assert ready.wait(60)
    with stream.sender() as sender:
        if kind == "send":
            for i in range(count):
                sender.send(i, timeout=60)
        else:

            async def run() -> None:
                for i in range(count):
                    await asyncio.wait_for(sender.asend(i), 60)

            asyncio.run(run())


def read_ints_as(
    name: str,
    kind: ReaderKind,
    opened: Event,
    go: Event,
    results: "Queue[list[int] | str]",
) -> None:
    """Open a lossless reader, wait for `go`, receive until the stream ends and report the integers or the traceback."""
    try:
        reader = SharedStream.attach(int, name).reader(start="oldest")
        opened.set()
        assert go.wait(60)
        got: list[int] = []
        match kind:
            case "receive":
                got.extend(reader)
            case "async":

                async def run() -> None:
                    got.extend([item async for item in reader])

                asyncio.run(run())
            case "signal":
                ended = threading.Event()
                reader.events.ended.connect(ended.set)
                reader.events.received.connect(lambda item, position: got.append(item))
                assert ended.wait(60)
        results.put(got)
    except BaseException:
        results.put(traceback.format_exc())
        raise
