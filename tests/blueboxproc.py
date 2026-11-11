"""Functions a spawned process runs against a bluebox channel, and the test side that starts them."""

import multiprocessing as mp
import os
import threading
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from types import TracebackType
from typing import Any, Literal, Self

import event_model
import numpy as np
from event_model.documents import Event, EventDescriptor, RunStart

from sharedbox.bluebox import DocumentReceiver, DocumentSender

CONTEXT = mp.get_context("spawn")
Document = tuple[str, dict[str, Any]]


def collect(
    name: str,
    mode: Literal["lossless", "lossy", "latest"],
    out: Connection,
    delay: float = 0.0,
    stops: int = 1,
) -> None:
    """Attach to channel `name`, send each document received through `out`, and report how it ended.

    The first message is `"ready"`, once attached. After `stops` stop
    documents the last message is `"done"`; an exception ends it with the
    exception's type name. A `delay` in seconds follows each event and event
    page.
    """
    try:
        with DocumentReceiver.attach(name, mode=mode) as receiver:
            out.send("ready")
            while stops > 0:
                pair = receiver.receive()
                out.send(pair)
                if pair[0] == "stop":
                    stops -= 1
                elif pair[0] in ("event", "event_page"):
                    time.sleep(delay)
        out.send("done")
    except Exception as error:
        out.send(type(error).__name__)


def reduce_arrays(
    send: Callable[[str, dict[str, Any]], None],
) -> event_model.DocumentRouter:
    """Return a router that sends, after each event, an event holding the sum of every array.

    The events belong to one descriptor named `"sums"`, which is sent after
    the first descriptor that holds an `Array1D` key.
    """

    class Sums(event_model.DocumentRouter):
        def __init__(self) -> None:
            super().__init__()
            self.start_doc: RunStart | None = None
            self.descriptors: dict[str, list[str]] = {}
            self.sums: Any = None
            self.counter: dict[str, int] = {}

        def start(self, doc: RunStart) -> RunStart:
            self.start_doc = doc
            return doc

        def descriptor(self, doc: EventDescriptor) -> EventDescriptor:
            keys = [
                key
                for key, data_key in doc["data_keys"].items()
                if data_key["dtype"] == "array" and len(data_key["shape"]) == 1
            ]
            if not keys or self.sums is not None:
                return doc
            assert self.start_doc is not None
            self.descriptors[doc["uid"]] = keys
            self.sums = event_model.compose_descriptor(
                start=self.start_doc,
                streams={},
                event_counters=self.counter,
                name="sums",
                data_keys={
                    key: {"source": f"sum of {key}", "dtype": "integer", "shape": []}
                    for key in keys
                },
            )
            send("descriptor", dict(self.sums.descriptor_doc))
            return doc

        def event(self, doc: Event) -> Event:
            keys = self.descriptors.get(doc["descriptor"])
            if keys is None:
                return doc
            send(
                "event",
                dict(
                    self.sums.compose_event(
                        data={key: int(np.sum(doc["data"][key])) for key in keys},
                        timestamps={key: doc["timestamps"][key] for key in keys},
                        seq_num=doc["seq_num"],
                    )
                ),
            )
            return doc

    return Sums()


def relay(
    src: str,
    dst: str,
    mode: Literal["lossless", "lossy", "latest"],
    transform: Literal["same", "sum"],
    out: Connection | None = None,
    **sender_options: Any,
) -> None:
    """Receive the documents of channel `src` until a stop and send them on channel `dst`.

    `sender_options` are the options of the new channel.

    With `"sum"`, each event with an `Array1D` key is followed by an event of
    a second descriptor holding `int(value.sum())` of each such key. The
    first message on `out` is `"ready"`; the last is `"done"` or the type
    name of the exception that ended the relay.
    """
    try:
        with (
            DocumentReceiver.attach(src, mode=mode) as receiver,
            DocumentSender.create(dst, **sender_options) as sender,
        ):
            router = reduce_arrays(sender) if transform == "sum" else None
            if out is not None:
                out.send("ready")
            for name, doc in receiver:
                sender(name, doc)
                if router is not None:
                    router(name, doc)
                if name == "stop":
                    break
        if out is not None:
            out.send("done")
    except Exception as error:
        if out is not None:
            out.send(type(error).__name__)


def run_and_die(name: str, out: Connection | None = None) -> None:
    """Start a run on a new channel `name`, send ten events and die without closing it.

    With `out`, the process sends `"ready"` after the descriptor and waits
    for a message from the test before it sends the events.
    """
    sender = DocumentSender.create(name)
    bundle = event_model.compose_run()
    sender("start", bundle.start_doc)
    descriptor = bundle.compose_descriptor(
        name="primary", data_keys={"x": {"source": "x", "dtype": "number", "shape": []}}
    )
    sender("descriptor", descriptor.descriptor_doc)
    if out is not None:
        out.send("ready")
        out.recv()
    for index in range(10):
        sender(
            "event",
            descriptor.compose_event(
                data={"x": float(index)}, timestamps={"x": 1.0}, seq_num=index + 1
            ),
        )
    os._exit(1)


class Child:
    """A spawned process and the messages it sends through its `out` pipe.

    A thread drains the pipe, so a child that sends many documents never
    waits for the test, which may itself be waiting for the child's
    receiver to read.
    """

    def __init__(
        self, target: Callable[..., None], *, duplex: bool = False, **kwargs: Any
    ) -> None:
        self.documents: list[Document] = []
        self.ending: str | None = None
        self._ready = threading.Event()
        self._connection, child_end = CONTEXT.Pipe(duplex=duplex)
        self.process = CONTEXT.Process(
            target=target, kwargs={**kwargs, "out": child_end}
        )
        self.process.start()
        child_end.close()
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()
        assert self._ready.wait(60), "the process did not become ready"

    def _drain(self) -> None:
        while True:
            try:
                message = self._connection.recv()
            except (EOFError, OSError):
                return
            if isinstance(message, tuple):
                self.documents.append(message)
            elif message == "ready":
                self._ready.set()
            else:
                self.ending = message

    def send(self, message: str) -> None:
        """Send `message` to a child started with `duplex=True`."""
        self._connection.send(message)

    def kill(self) -> None:
        """Kill the process."""
        self.process.kill()

    def result(self, timeout: float = 60) -> tuple[list[Document], str]:
        """Wait for the process and return the documents it sent and how it ended.

        A process that ended without a message, such as a killed one, ends
        with `"killed"`.
        """
        self.process.join(timeout)
        assert not self.process.is_alive(), "the process did not finish"
        self._thread.join(10)
        return self.documents, self.ending or "killed"

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.process.is_alive():
            self.process.kill()
        self.process.join(10)
        self._connection.close()
