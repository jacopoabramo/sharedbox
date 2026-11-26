import contextlib
import dataclasses
import math
import time
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from capsule_consumer import Producer, Sample, Settings, _producer

from sharedbox import (
    EndOfStream,
    SharedBox,
    SharedReceiveStream,
    SharedStream,
    get_schema_hash,
)


@pytest.fixture
def name() -> Generator[str, None, None]:
    """Give a fresh segment name, and remove the box and stream made under it afterwards."""
    name = f"capsule-consumer-{uuid.uuid4().hex[:12]}"
    yield name
    with contextlib.suppress(FileNotFoundError):
        Settings.unlink(name)
    with contextlib.suppress(FileNotFoundError):
        SharedStream.unlink(name + "-items")


def receive_all(reader: SharedReceiveStream[Sample, Any]) -> list[Sample]:
    """Receive until the producer ends the stream."""
    received = []
    with contextlib.suppress(EndOfStream):
        while True:
            received.append(reader.receive(timeout=5.0))
    return received


def test_the_producer_sends_count_samples_in_order(name: str) -> None:
    """Send count samples, each computed from its position, then end the stream."""
    with (
        Settings.create(name, rate_hz=10_000.0, count=100) as settings,
        SharedStream.create(Sample, name + "-items", capacity=16) as stream,
    ):
        reader = stream.reader(mode="lossless")
        producer = Producer(settings, stream)
        producer.start()
        received = receive_all(reader)
        producer.stop()
    assert received == [Sample(i, i * 0.5) for i in range(100)]


def test_the_thread_reads_the_box_while_it_runs(name: str) -> None:
    """Stop after the count Python writes into the box while the thread runs."""
    with (
        Settings.create(name, rate_hz=20.0, count=0) as settings,
        SharedStream.create(Sample, name + "-items", capacity=16) as stream,
    ):
        reader = stream.reader(mode="lossless")
        with Producer(settings, stream):
            first = [reader.receive(timeout=5.0) for _ in range(3)]
            settings.count = 10
            rest = receive_all(reader)
    assert [sample.index for sample in first + rest] == list(range(10))


def test_a_count_below_what_was_sent_ends_the_stream(name: str) -> None:
    """End the stream without an error when Python lowers count below the samples already sent."""
    with (
        Settings.create(name, rate_hz=20.0, count=0) as settings,
        SharedStream.create(Sample, name + "-items", capacity=16) as stream,
    ):
        reader = stream.reader(mode="lossless")
        producer = Producer(settings, stream)
        producer.start()
        first = [reader.receive(timeout=5.0) for _ in range(3)]
        settings.count = 1
        rest = receive_all(reader)
        producer.stop()
    # The thread may have sent one more sample before it read the new count.
    assert [sample.index for sample in first + rest] in (
        [0, 1, 2],
        [0, 1, 2, 3],
    )


def test_a_box_of_another_class_is_refused(name: str) -> None:
    """Refuse a box whose schema hash is not that of Settings."""

    class Other(SharedBox):
        a: float = 0.0
        b: int = 0

    with (
        Other.create(name) as other,
        SharedStream.create(Sample, name + "-items", capacity=4) as stream,
        pytest.raises(TypeError, match="expected schema"),
    ):
        Producer(other, stream)


@dataclasses.dataclass(frozen=True)
class Swapped:
    """Sample's members in the other order: 16 bytes, like Sample."""

    value: float
    index: int


def test_an_item_type_the_thread_does_not_write_is_refused(name: str) -> None:
    """Refuse a 16-byte item whose members differ from Item, even when the hash agrees."""
    _producer.set_schemas(get_schema_hash(Settings), get_schema_hash(Swapped))
    try:
        with (
            Settings.create(name) as settings,
            SharedStream.create(Swapped, name + "-items", capacity=4) as stream,
            pytest.raises(TypeError, match="member 0"),
        ):
            Producer(settings, stream)
    finally:
        _producer.set_schemas(get_schema_hash(Settings), get_schema_hash(Sample))


def test_a_second_producer_on_one_stream_is_refused(name: str) -> None:
    """Refuse a second producer, since a stream has one sender."""
    with (
        Settings.create(name) as settings,
        SharedStream.create(Sample, name + "-items", capacity=4) as stream,
    ):
        first = Producer(settings, stream)
        with pytest.raises(RuntimeError, match="busy"):
            Producer(settings, stream)
        first.stop()


def test_an_error_in_the_thread_reaches_stop(name: str) -> None:
    """Raise from stop the timeout of a send that a full ring held back."""
    with (
        Settings.create(name) as settings,
        SharedStream.create(Sample, name + "-items", capacity=8) as stream,
    ):
        reader = stream.reader(mode="lossless")
        producer = Producer(settings, stream)
        producer.start()
        # The ninth send times out after 1 s; the rest is room for a slow runner.
        time.sleep(3.0)
        with pytest.raises(RuntimeError, match="timeout"):
            producer.stop()
        reader.close()


def test_stop_interrupts_a_waiting_send(name: str) -> None:
    """Return from stop at once, without an error, while a send waits on a full ring."""
    # At 1 MHz the ring fills at once, so by the stop the ninth send waits, within its 1 s timeout.
    with (
        Settings.create(name, rate_hz=1e6) as settings,
        SharedStream.create(Sample, name + "-items", capacity=8) as stream,
    ):
        reader = stream.reader(mode="lossless")
        producer = Producer(settings, stream)
        producer.start()
        time.sleep(0.5)
        began = time.monotonic()
        producer.stop()
        assert time.monotonic() - began < 0.5
        assert len(receive_all(reader)) == 8


def test_stop_ends_a_read_waiting_for_the_box_lock(name: str) -> None:
    """Return from stop at once, without an error, while the thread waits for a lock Python holds."""
    with (
        Settings.create(name) as settings,
        SharedStream.create(Sample, name + "-items", capacity=16) as stream,
    ):
        reader = stream.reader(mode="lossy")
        producer = Producer(settings, stream)
        producer.start()
        reader.receive(timeout=5.0)
        with settings.locked():
            time.sleep(0.2)
            began = time.monotonic()
            producer.stop()
            assert time.monotonic() - began < 0.5


@pytest.mark.parametrize("rate_hz", [0.0, -1.0, math.nan])
def test_a_rate_that_is_not_positive_is_an_error(name: str, rate_hz: float) -> None:
    """Raise from stop when the box holds a rate the thread cannot wait for."""
    with (
        Settings.create(name, rate_hz=rate_hz) as settings,
        SharedStream.create(Sample, name + "-items", capacity=4) as stream,
    ):
        producer = Producer(settings, stream)
        producer.start()
        with pytest.raises(RuntimeError, match="rate_hz"):
            producer.stop()


def test_a_negative_count_is_an_error(name: str) -> None:
    """Raise from stop when the box holds a count below 0, instead of sending without limit."""
    with (
        Settings.create(name, count=-1) as settings,
        SharedStream.create(Sample, name + "-items", capacity=4) as stream,
    ):
        producer = Producer(settings, stream)
        producer.start()
        with pytest.raises(RuntimeError, match="count"):
            producer.stop()


def test_a_tiny_rate_waits_instead_of_sending_at_full_speed(name: str) -> None:
    """Send one sample and then wait, for a rate whose period overflows a clock's range."""
    with (
        Settings.create(name, rate_hz=1e-300) as settings,
        SharedStream.create(Sample, name + "-items", capacity=16) as stream,
    ):
        reader = stream.reader(mode="lossless")
        with Producer(settings, stream):
            assert reader.receive(timeout=5.0) == Sample(0, 0.0)
            with pytest.raises(TimeoutError):
                reader.receive(timeout=0.3)


def test_start_twice_is_refused(name: str) -> None:
    """Refuse a second start while the thread runs, and leave that thread running."""
    with (
        Settings.create(name, rate_hz=100.0) as settings,
        SharedStream.create(Sample, name + "-items", capacity=4) as stream,
    ):
        reader = stream.reader(mode="lossy")
        producer = Producer(settings, stream)
        producer.start()
        with pytest.raises(RuntimeError, match="running"):
            producer.start()
        reader.receive(timeout=5.0)
        producer.stop()
