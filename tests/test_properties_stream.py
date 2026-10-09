import uuid
from dataclasses import dataclass

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from sharedbox import SharedStream, StreamReader, StreamSender, WouldBlock

CAPACITY = 4
MAX_READERS = 4


@dataclass
class Model:
    """What a reader should do, in the stream ring's own terms: the next position and the items skipped."""

    reader: StreamReader[int]
    mode: str
    position: int
    missed: int = 0


class StreamMachine(RuleBasedStateMachine):
    """One stream with one sender and several readers, against a list of what was sent."""

    def __init__(self) -> None:
        super().__init__()
        self.name = ""
        self.stream: SharedStream[int]
        self.sender: StreamSender[int]
        self.sent: list[int] = []
        self.readers: list[Model] = []

    @initialize()
    def create(self) -> None:
        """Create the stream and take its sender."""
        self.name = f"sbtest-{uuid.uuid4().hex[:16]}"
        self.stream = SharedStream.create(
            int, self.name, capacity=CAPACITY, max_readers=MAX_READERS
        )
        self.sender = self.stream.sender()

    def blocked(self) -> bool:
        """Whether some lossless reader is a full ring behind."""
        return any(
            m.mode == "lossless" and len(self.sent) >= m.position + CAPACITY
            for m in self.readers
        )

    @rule(value=st.integers(-(2**63), 2**63 - 1))
    def send(self, value: int) -> None:
        """Send without waiting: WouldBlock exactly when a lossless reader is a full ring behind."""
        if self.blocked():
            with pytest.raises(WouldBlock):
                self.sender.send_nowait(value)
        else:
            self.sender.send_nowait(value)
            self.sent.append(value)

    @precondition(lambda self: len(self.readers) < MAX_READERS)
    @rule(mode=st.sampled_from(["lossless", "lossy", "latest"]), oldest=st.booleans())
    def open_reader(self, mode: str, oldest: bool) -> None:
        """Open a reader at the newest or the oldest buffered item."""
        n = len(self.sent)
        position = max(n - CAPACITY, 0) if oldest else max(n - 1, 0)
        reader = self.stream.reader(mode=mode, start="oldest" if oldest else "newest")  # type: ignore[arg-type]
        self.readers.append(Model(reader, mode, position))

    @precondition(lambda self: bool(self.readers))
    @rule(data=st.data())
    def receive(self, data: st.DataObject) -> None:
        """Receive without waiting and expect the item the mode's rules give, or WouldBlock."""
        model = data.draw(st.sampled_from(self.readers))
        n = len(self.sent)
        if model.position == n:
            with pytest.raises(WouldBlock):
                model.reader.receive_nowait()
            return
        behind = {"lossless": 0, "lossy": n - CAPACITY, "latest": n - 1}[model.mode]
        if model.position < behind:
            model.missed += behind - model.position
            model.position = behind
        assert model.reader.receive_nowait() == self.sent[model.position]
        assert model.reader.position == model.position
        model.position += 1

    @precondition(lambda self: bool(self.readers))
    @rule(data=st.data())
    def close_reader(self, data: st.DataObject) -> None:
        """Close a reader; it no longer holds the sender back."""
        model = data.draw(st.sampled_from(self.readers))
        model.reader.close()
        self.readers.remove(model)

    @invariant()
    def statistics_match_the_model(self) -> None:
        """The stream reports what was sent and each open reader's mode and next position."""
        if not self.name:
            return
        stats = self.stream.statistics()
        assert stats.sent == len(self.sent)
        assert sorted((r.mode, r.position) for r in stats.readers) == sorted(
            (m.mode, m.position) for m in self.readers
        )
        for model in self.readers:
            assert model.reader.missed == model.missed

    def teardown(self) -> None:
        if self.name:
            self.stream.close()
            SharedStream.unlink(self.name)


TestStreamMachine = StreamMachine.TestCase
