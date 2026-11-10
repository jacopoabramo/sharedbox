"""Devices and plans that produce bluesky documents for the bluebox tests."""

from typing import Any

import numpy as np
from blueboxproc import Document
from bluesky.run_engine import RunEngine
from ophyd_async.core import (
    Array1D,
    AsyncStatus,
    StandardReadable,
    soft_signal_r_and_setter,
)


class ArrayDetector(StandardReadable):
    """A detector whose one key is a `uint16` array that changes with each trigger."""

    def __init__(self, name: str = "") -> None:
        self.triggered = 0
        with self.add_children_as_readables():
            self.spectrum, self._set_spectrum = soft_signal_r_and_setter(
                Array1D[np.uint16], np.zeros(8, dtype=np.uint16)
            )
        super().__init__(name)

    @AsyncStatus.wrap
    async def trigger(self) -> None:
        self.triggered += 1
        self._set_spectrum(np.arange(8, dtype=np.uint16) + self.triggered)


def new_engine() -> RunEngine:
    """Return a run engine that sends a plan's return value back from the call."""
    return RunEngine(call_returns_result=True)


def run(engine: RunEngine, *plans: Any) -> list[Document]:
    """Run each plan and return the documents a list subscribed to the engine received."""
    documents: list[Document] = []
    token = engine.subscribe(lambda name, doc: documents.append((name, doc)))
    try:
        for plan in plans:
            engine(plan)
    finally:
        engine.unsubscribe(token)
    return documents
