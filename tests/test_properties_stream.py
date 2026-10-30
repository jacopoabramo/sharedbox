import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import cbor2
import numpy as np
import numpy.typing as npt
import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from sharedbox import (
    MaxShape,
    MetadataFullError,
    ReaderReleased,
    SharedReceiveStream,
    SharedSendStream,
    SharedStream,
    WouldBlock,
)

CAPACITY = 4
MAX_READERS = 4
METADATA_VERSIONS = 4
METADATA_CAPACITY = 128


@dataclass
class Model:
    """What a reader should do, in the stream ring's own terms: the next position and the items skipped."""

    reader: SharedReceiveStream[int]
    mode: str
    position: int
    missed: int = 0


class StreamMachine(RuleBasedStateMachine):
    """One stream with one sender and several readers, against a list of what was sent."""

    on_full: Literal["wait", "release"] = "wait"

    def __init__(self) -> None:
        super().__init__()
        self.name = ""
        self.stream: SharedStream[int]
        self.sender: SharedSendStream[int]
        self.sent: list[int] = []
        self.readers: list[Model] = []
        self.floor = 0

    @initialize()
    def create(self) -> None:
        """Create the stream and take its sender."""
        self.name = f"sbtest-{uuid.uuid4().hex[:16]}"
        self.stream = SharedStream.create(
            int, self.name, capacity=CAPACITY, max_readers=MAX_READERS
        )
        self.sender = self.stream.sender(on_full=self.on_full)

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
        """Open a reader at the newest or the oldest buffered item; a lossless one starts no lower than the release floor."""
        n = len(self.sent)
        position = max(n - CAPACITY, 0) if oldest else max(n - 1, 0)
        if mode == "lossless":
            position = max(position, self.floor)
        reader = self.stream.reader(mode=mode, start="oldest" if oldest else "newest")  # type: ignore[arg-type]
        self.readers.append(Model(reader, mode, position))

    @precondition(lambda self: bool(self.readers))
    @rule(data=st.data())
    def receive(self, data: st.DataObject) -> None:
        """Receive without waiting and expect the item the mode's rules give, or WouldBlock."""
        self.expect_receive(data.draw(st.sampled_from(self.readers)))

    def expect_receive(self, model: Model) -> None:
        """Receive from `model`'s reader and check the result against the model."""
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


Cube = Annotated[npt.NDArray[np.uint16], MaxShape((5, 4, 3))]
CUBES = st.tuples(*(st.integers(0, m) for m in (5, 4, 3))).flatmap(
    lambda shape: arrays(np.uint16, shape)
)


@given(st.lists(CUBES, min_size=1, max_size=6))
def test_max_shape_arrays_come_back_equal(cubes: list[npt.NDArray[np.uint16]]) -> None:
    """Receive each MaxShape array sent equal, through receive and through receive_into one reused out."""
    name = f"sbtest-{uuid.uuid4().hex[:16]}"
    out = np.zeros((5, 4, 3), np.uint16)
    try:
        with SharedStream.create(Cube, name, capacity=2) as stream:
            plain = stream.reader()
            into = stream.reader()
            sender = stream.sender()
            for cube in cubes:
                sender.send(cube)
                np.testing.assert_array_equal(plain.receive(timeout=5), cube)
                got = into.receive_into(out, timeout=5)
                assert got.shape == cube.shape
                np.testing.assert_array_equal(got, cube)
    finally:
        SharedStream.unlink(name)


class ReleaseMachine(StreamMachine):
    """The same stream with a sender that releases lossless readers instead of failing."""

    on_full: Literal["wait", "release"] = "release"

    @rule(value=st.integers(-(2**63), 2**63 - 1))
    def send(self, value: int) -> None:
        """Send without waiting; a full ring behind raises the floor of the lossless readers."""
        n = len(self.sent)
        if any(
            m.mode == "lossless"
            and m.position >= self.floor
            and n >= m.position + CAPACITY
            for m in self.readers
        ):
            self.floor = max(self.floor, n - CAPACITY + 1)
        self.sender.send_nowait(value)
        self.sent.append(value)

    def expect_receive(self, model: Model) -> None:
        """Receive from `model`'s reader; a lossless one below the floor is released first."""
        n = len(self.sent)
        if model.mode == "lossless" and model.position < self.floor:
            resume = max(n - 1, self.floor)
            with pytest.raises(ReaderReleased) as released:
                model.reader.receive_nowait()
            assert released.value.missed == resume - model.position
            model.missed += resume - model.position
            model.position = resume
            return
        super().expect_receive(model)


TestReleaseMachine = ReleaseMachine.TestCase


@dataclass(frozen=True)
class Version:
    """A metadata version as the area stores it: the position it applies from, its place in the ring and its document."""

    position: int
    offset: int
    size: int
    document: dict[str, Any]


class MetadataMachine(RuleBasedStateMachine):
    """One stream with metadata, its sender and a lossless reader, against a model of the metadata area."""

    def __init__(self) -> None:
        super().__init__()
        self.name = ""
        self.stream: SharedStream[int]
        self.sender: SharedSendStream[int]
        self.reader: SharedReceiveStream[int]
        self.sent = 0
        self.received = 0
        self.versions: list[Version] = []
        self.held: set[int] = set()

    @initialize(first=st.booleans())
    def create(self, first: bool) -> None:
        """Create the stream, with version 1 or with no version, take its sender and open a reader."""
        self.name = f"sbtest-{uuid.uuid4().hex[:16]}"
        metadata = self.document(1, 0) if first else None
        self.stream = SharedStream.create(
            int,
            self.name,
            capacity=CAPACITY,
            metadata=metadata,
            metadata_capacity=METADATA_CAPACITY,
            metadata_versions=METADATA_VERSIONS,
        )
        self.sender = self.stream.sender()
        self.reader = self.stream.reader(start="oldest")
        if metadata is not None:
            self.store(metadata)

    @staticmethod
    def document(version: int, pad: int) -> dict[str, Any]:
        """The document of `version`, `pad` characters longer than the shortest."""
        return {"version": version, "pad": "x" * pad}

    def store(self, document: dict[str, Any]) -> None:
        """Record `document` as the next version, at the place the area writes it, and drop the versions it overwrites."""
        size = len(cbor2.dumps(document, canonical=True))
        at = self.place(size)
        self.held -= set(self.overwritten(at, size))
        self.versions.append(Version(self.sent, at, size, document))
        self.held.add(len(self.versions))

    def place(self, size: int) -> int:
        """The ring offset of the next document: after the newest, or 0 when it does not fit there."""
        if not self.versions:
            return 0
        newest = self.versions[-1]
        end = -(-(newest.offset + newest.size) // 8) * 8
        return end if end + size <= METADATA_CAPACITY else 0

    def overwritten(self, at: int, size: int) -> list[int]:
        """The kept versions older than the newest whose entry or bytes the next document takes, oldest first."""
        reused = len(self.versions) + 1 - METADATA_VERSIONS
        return [
            u
            for u in sorted(self.held)
            if u < len(self.versions)
            and (
                u == reused
                or (
                    at < self.versions[u - 1].offset + self.versions[u - 1].size
                    and self.versions[u - 1].offset < at + size
                )
            )
        ]

    def full(self, document: dict[str, Any]) -> bool:
        """Whether adding `document` would overwrite a version that a reader may still need.

        A version is needed while an item in the ring was sent under it, or,
        for version 1, before it. A version with no items is needed as long
        as an older version is kept, until the items of that span leave the
        ring.
        """
        size = len(cbor2.dumps(document, canonical=True))
        gone = self.overwritten(self.place(size), size)
        for u in gone:
            start, end = self.versions[u - 1].position, self.versions[u].position
            older_kept = any(w < u and w not in gone for w in self.held)
            covers = start != end or (u == 1 and start != 0) or older_kept
            if covers and end + CAPACITY > self.sent:
                return True
        return False

    @precondition(lambda self: self.sent < self.received + CAPACITY)
    @rule()
    def send(self) -> None:
        """Send the next position as the item."""
        self.sender.send_nowait(self.sent)
        self.sent += 1

    @precondition(lambda self: self.sent < self.received + CAPACITY)
    @rule(pad=st.integers(0, 20))
    def send_with_metadata(self, pad: int) -> None:
        """Send the next position with the next version, which applies from it; MetadataFullError, sending nothing, exactly when it would overwrite a version a reader may still need."""
        document = self.document(len(self.versions) + 1, pad)
        if self.full(document):
            with pytest.raises(MetadataFullError):
                self.sender.send_nowait(self.sent, metadata=document)
            assert self.stream.metadata_version == len(self.versions)
            return
        self.sender.send_nowait(self.sent, metadata=document)
        self.store(document)
        self.sent += 1

    @precondition(lambda self: self.received < self.sent)
    @rule()
    def receive(self) -> None:
        """Receive the next item and see the newest version added at or before its position, 0 and no metadata before version 1."""
        assert self.reader.receive_nowait() == self.received
        version = max(
            (
                v
                for v, added in enumerate(self.versions, start=1)
                if added.position <= self.received
            ),
            default=0,
        )
        assert self.reader.metadata_version == version
        assert self.reader.metadata == (
            self.versions[version - 1].document if version else None
        )
        self.received += 1

    def teardown(self) -> None:
        if self.name:
            self.stream.close()
            SharedStream.unlink(self.name)


TestMetadataMachine = MetadataMachine.TestCase
