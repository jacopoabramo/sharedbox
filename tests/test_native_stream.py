import threading
import time
from dataclasses import dataclass
from typing import Annotated

import numpy as np
import pytest

from sharedbox import (
    Capacity,
    DType,
    KindMismatchError,
    SchemaMismatchError,
    Shape,
    SharedBox,
)
from sharedbox._native import (
    EndOfStream,
    Reader,
    Stream,
    StreamBusyError,
    StreamClosedError,
    Types,
    WaiterSlotsFullError,
    WouldBlock,
)
from sharedbox._types import Table, parse

LOSSLESS, LOSSY, LATEST = 1, 2, 3


@dataclass(frozen=True)
class Point:
    x: int
    label: Annotated[str, Capacity(8)]


def item(hint: object) -> tuple[Types, int, int]:
    """Return the one-field Types, item entry and schema hash `_stream.py` builds for `hint`."""
    spec = parse(hint, "item")
    table = Table()
    low = table.describe(spec) if spec.described else spec.entry_low
    types = Types(
        [(0, low, spec.code)], ["item"], bytes(table.data), table.info, table.bytearrays
    )
    return types, low | spec.code << 24, hash(spec.text) & 0xFFFFFFFFFFFFFFFF


def create(name: str, hint: object, capacity: int = 4, max_readers: int = 2) -> Stream:
    types, entry, schema = item(hint)
    return Stream.create(name, types, entry, schema, capacity, max_readers)


def test_items_of_each_kind_come_back(unique_name: str) -> None:
    """Send an int, a str, a record and an array and receive equal values."""
    for suffix, hint, value in [
        ("i", int, 7),
        ("s", Annotated[str, Capacity(8)], "abc"),
        ("r", Point, Point(3, "p")),
        (
            "a",
            Annotated[np.ndarray, Shape(2, 3), DType("float32")],
            np.arange(6, dtype=np.float32).reshape(2, 3),
        ),
    ]:
        stream = create(f"{unique_name}-{suffix}", hint)
        reader = stream.reader(LOSSLESS, True)
        sender = stream.sender()
        assert sender.send(value, 0.0) == 0
        got, position = reader.receive(0.0)
        assert position == 0
        np.testing.assert_equal(got, value)
        stream.close()


def test_receive_with_no_item_raises_would_block_at_once(unique_name: str) -> None:
    """Raise WouldBlock within 50 ms for a receive with a timeout of 0 on an empty stream."""
    stream = create(unique_name, int)
    reader = stream.reader(LOSSLESS, True)
    start = time.perf_counter()
    with pytest.raises(WouldBlock):
        reader.receive(0.0)
    assert time.perf_counter() - start < 0.05


def test_a_closed_sender_ends_the_stream_after_what_is_buffered(
    unique_name: str,
) -> None:
    """Receive what was sent before the sender closed, then raise EndOfStream."""
    stream = create(unique_name, int)
    reader = stream.reader(LOSSLESS, True)
    sender = stream.sender()
    sender.send(1, 0.0)
    sender.close()
    assert reader.receive(0.0)[0] == 1
    with pytest.raises(EndOfStream):
        reader.receive(0.0)
    with pytest.raises(EndOfStream):
        stream.sender()


def test_a_second_sender_is_busy(unique_name: str) -> None:
    """Raise StreamBusyError for a second sender while the first is open."""
    stream = create(unique_name, int)
    first = stream.sender()
    with pytest.raises(StreamBusyError):
        stream.sender()
    first.close()


def test_a_lossy_reader_counts_what_it_missed(unique_name: str) -> None:
    """Skip to the oldest item the ring holds and count the overwritten ones."""
    stream = create(unique_name, int, capacity=2)
    reader = stream.reader(LOSSY, True)
    sender = stream.sender()
    for value in range(5):
        sender.send(value, 0.0)
    assert reader.receive(0.0) == (3, 3)
    assert reader.missed == 3


def test_more_readers_than_max_readers_are_refused(unique_name: str) -> None:
    """Raise WaiterSlotsFullError for a reader beyond max_readers."""
    stream = create(unique_name, int, max_readers=1)
    first = stream.reader(LOSSLESS, True)
    with pytest.raises(WaiterSlotsFullError):
        stream.reader(LOSSY, True)
    first.close()


def test_attach_checks_the_item_type(unique_name: str) -> None:
    """Attach with the same item type, and raise SchemaMismatchError for another."""
    stream = create(unique_name, int)
    types, entry, schema = item(int)
    Stream.attach(unique_name, types, entry, schema).close()
    other, entry, schema = item(float)
    with pytest.raises(SchemaMismatchError, match="another item type"):
        Stream.attach(unique_name, other, entry, schema)
    stream.close()


def test_a_box_and_a_stream_refuse_each_others_names(unique_name: str) -> None:
    """Raise KindMismatchError naming both kinds when a box name is opened as a stream, and the other way."""

    class Counter(SharedBox, name=f"{unique_name}-box"):
        value: int = 0

    with Counter():
        types, entry, schema = item(int)
        with pytest.raises(KindMismatchError, match="is a box, not a stream"):
            Stream.attach(f"{unique_name}-box", types, entry, schema)
    stream = create(f"{unique_name}-stream", int)
    with pytest.raises(KindMismatchError, match="is a stream, not a box"):
        Counter.attach(f"{unique_name}-stream")
    stream.close()


def test_a_closed_end_raises_stream_closed(unique_name: str) -> None:
    """Raise StreamClosedError from a reader and a sender used after close."""
    stream = create(unique_name, int)
    reader = stream.reader(LOSSLESS, True)
    sender = stream.sender()
    reader.close()
    sender.close()
    with pytest.raises(StreamClosedError):
        reader.receive(0.0)
    with pytest.raises(StreamClosedError):
        sender.send(1, 0.0)


def test_statistics_report_the_sender_and_readers(unique_name: str) -> None:
    """Report items sent, the sender's pid and each reader's position, mode and pid."""
    stream = create(unique_name, int)
    reader = stream.reader(LOSSY, True)
    sender = stream.sender()
    sender.send(1, 0.0)
    reader.receive(0.0)
    sent, ended, pid, readers = stream.statistics()
    assert (sent, ended) == (1, False)
    assert pid > 0
    assert readers == [(1, LOSSY, pid)]


def test_a_negative_timeout_is_refused(unique_name: str) -> None:
    """Raise ValueError for a timeout below 0."""
    reader: Reader = create(unique_name, int).reader(LOSSLESS, True)
    with pytest.raises(ValueError):
        reader.receive(-1.0)


def test_close_ends_a_blocked_receive(unique_name: str) -> None:
    """Raise StreamClosedError within a step in a receive another thread's close() interrupted."""
    reader = create(unique_name, int).reader(LOSSLESS, True)
    raised: list[BaseException] = []

    def wait() -> None:
        try:
            reader.receive(1.0)
        except BaseException as error:
            raised.append(error)

    thread = threading.Thread(target=wait)
    thread.start()
    time.sleep(0.1)
    start = time.perf_counter()
    reader.close()
    thread.join(2.0)
    assert time.perf_counter() - start < 1.0
    assert [type(error) for error in raised] == [StreamClosedError]
