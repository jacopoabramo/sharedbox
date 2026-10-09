import gc
import pickle
import re
import subprocess
import sys
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Annotated, Any

import numpy as np
import pytest

from sharedbox import (
    Capacity,
    DType,
    EndOfStream,
    ReaderStatistics,
    SchemaMismatchError,
    Shape,
    SharedBox,
    SharedStream,
    StreamBusyError,
    StreamClosedError,
    StreamReader,
    WouldBlock,
)


@dataclass(frozen=True)
class Frame:
    index: int
    image: Annotated[np.ndarray, Shape(4, 4), DType("uint16")]


def frame(i: int) -> Frame:
    return Frame(i, np.full((4, 4), i, np.uint16))


def test_items_go_from_the_sender_to_a_reader(unique_name: str) -> None:
    """Receive every item sent, in order, then EndOfStream after the sender closes."""
    with SharedStream.create(Frame, unique_name, capacity=4) as stream:
        reader = stream.reader()
        with stream.sender() as sender:
            for i in range(3):
                sender.send(frame(i))
        assert [item.index for item in reader] == [0, 1, 2]
        assert reader.position == 2


def test_receive_into_fills_the_given_array(unique_name: str) -> None:
    """Return items whose image is the caller's array, refilled by each receive."""
    with SharedStream.create(Frame, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()
        sender.send(frame(5))
        sender.send(frame(6))
        image = np.zeros((4, 4), np.uint16)
        first = reader.receive_into({"image": image})
        assert first.image is image
        assert first.image[0, 0] == 5
        second = next(reader.iter_into({"image": image, "index": None}))
        assert second.image is image
        assert image[0, 0] == 6


def test_receive_into_takes_a_tuple_for_a_tuple_item(unique_name: str) -> None:
    """Fill the array entry of a tuple out and decode the member given as None."""
    hint = tuple[
        Annotated[
            np.ndarray,
            Shape(
                3,
            ),
            DType("float32"),
        ],
        int,
    ]
    with SharedStream.create(hint, unique_name, capacity=2) as stream:
        reader = stream.reader()
        stream.sender().send((np.ones(3, np.float32), 4))
        line = np.zeros(3, np.float32)
        got = reader.receive_into((line, None), timeout=5)
        assert got[0] is line
        assert got[1] == 4


@pytest.mark.parametrize(
    "out",
    [
        {"pixels": np.zeros((4, 4), np.uint16)},
        ({"image": np.zeros((4, 4), np.uint16)},),
        np.zeros((4, 4), np.uint16),
        {"index": np.zeros(1, np.int64)},
    ],
)
def test_receive_into_refuses_an_out_of_the_wrong_shape_and_keeps_the_item(
    unique_name: str, out: object
) -> None:
    """Raise TypeError for an unknown member, an out of another shape or an array for a non-array member, and keep the item."""
    with SharedStream.create(Frame, unique_name, capacity=2) as stream:
        reader = stream.reader()
        stream.sender().send(frame(1))
        with pytest.raises(TypeError):
            reader.receive_into_nowait(out)
        assert reader.receive_nowait().index == 1


@dataclass(frozen=True)
class Inner:
    image: Annotated[
        np.ndarray,
        Shape(
            2,
        ),
        DType("uint8"),
    ]


@dataclass(frozen=True)
class Outer:
    inner: Inner
    pair: tuple[
        Annotated[
            np.ndarray,
            Shape(
                2,
            ),
            DType("uint8"),
        ],
        int,
    ]


def test_receive_into_fills_nested_arrays(unique_name: str) -> None:
    """Fill the arrays of a record in a record and a tuple in a record."""
    with SharedStream.create(Outer, unique_name, capacity=2) as stream:
        reader = stream.reader()
        stream.sender().send(
            Outer(Inner(np.full(2, 3, np.uint8)), (np.full(2, 4, np.uint8), 9))
        )
        a = np.zeros(2, np.uint8)
        b = np.zeros(2, np.uint8)
        got = reader.receive_into({"inner": {"image": a}, "pair": (b, None)}, timeout=5)
        assert got.inner.image is a and got.pair[0] is b
        assert a[0] == 3 and b[0] == 4 and got.pair[1] == 9


@pytest.mark.parametrize(
    "out",
    [
        {"pair": (np.zeros(2, np.uint8),)},
        {"inner": {"bad": np.zeros(2, np.uint8)}},
    ],
)
def test_receive_into_refuses_a_wrong_nested_out_and_keeps_the_item(
    unique_name: str, out: object
) -> None:
    """Raise TypeError for a tuple of the wrong length or an unknown nested member, and keep the item."""
    with SharedStream.create(Outer, unique_name, capacity=2) as stream:
        reader = stream.reader()
        stream.sender().send(
            Outer(Inner(np.zeros(2, np.uint8)), (np.zeros(2, np.uint8), 1))
        )
        with pytest.raises(TypeError):
            reader.receive_into_nowait(out)
        assert reader.receive_nowait().pair[1] == 1


def test_receive_into_refuses_an_array_for_a_collection_member(
    unique_name: str,
) -> None:
    """Raise TypeError for an array given where the record holds a list, before anything is received."""

    @dataclass(frozen=True)
    class Tagged:
        tags: Annotated[list[int], Capacity(4)]
        image: Annotated[np.ndarray, Shape(2), DType("uint8")]

    with SharedStream.create(Tagged, unique_name, capacity=2) as stream:
        reader = stream.reader()
        stream.sender().send(Tagged([1], np.zeros(2, np.uint8)))
        with pytest.raises(TypeError, match="tags"):
            reader.receive_into_nowait({"tags": np.zeros(4, np.int64)})
        assert reader.receive_nowait().tags == [1]


@pytest.mark.parametrize(
    ("hint", "message"),
    [(int | None, "is an optional member"), (int | float, "is a union member")],
)
def test_receive_into_refuses_an_array_for_an_optional_or_union_member(
    unique_name: str, hint: Any, message: str
) -> None:
    """Raise TypeError naming the member's kind for an array given for an optional or union member."""
    with SharedStream.create(tuple[Frame, hint], unique_name, capacity=2) as stream:
        reader = stream.reader()
        with pytest.raises(TypeError, match=message):
            reader.receive_into_nowait((None, np.zeros(2, np.int64)))


def test_receive_into_refuses_an_item_without_arrays(unique_name: str) -> None:
    """Raise TypeError from receive_into on a collection item, which holds no array, before anything is received."""
    hint = Annotated[list[int], Capacity(4)]
    with SharedStream.create(hint, unique_name, capacity=2) as stream:
        reader = stream.reader()
        stream.sender().send([1, 2])
        with pytest.raises(TypeError, match="no array"):
            reader.receive_into_nowait(None)
        assert reader.receive_nowait() == [1, 2]


def test_nowait_calls_raise_would_block(unique_name: str) -> None:
    """Raise WouldBlock from receive_nowait on an empty stream and send_nowait on a full lossless ring."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        reader = stream.reader()
        with pytest.raises(WouldBlock):
            reader.receive_nowait()
        sender = stream.sender()
        sender.send_nowait(1)
        sender.send_nowait(2)
        with pytest.raises(WouldBlock):
            sender.send_nowait(3)


def test_a_finite_timeout_raises_timeout_error(unique_name: str) -> None:
    """Raise TimeoutError once a receive's timeout passes."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            stream.reader().receive(timeout=0.2)
        assert 0.15 < time.monotonic() - start < 1.0


def test_a_timeout_over_one_step_still_raises_timeout_error(unique_name: str) -> None:
    """Wait across more than one native step, then raise TimeoutError."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            stream.reader().receive(timeout=1.3)
        assert 1.3 <= time.monotonic() - start < 3.0


def test_close_ends_a_blocked_receive(unique_name: str) -> None:
    """End a receive waiting with no timeout when another thread closes the reader."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        reader = stream.reader()
        raised: list[BaseException] = []

        def wait() -> None:
            try:
                reader.receive()
            except BaseException as error:
                raised.append(error)

        thread = threading.Thread(target=wait)
        thread.start()
        time.sleep(0.2)
        reader.close()
        thread.join(2.0)
        assert not thread.is_alive()
        assert isinstance(raised[0], StreamClosedError)


def test_a_second_sender_is_busy(unique_name: str) -> None:
    """Raise StreamBusyError for a second sender while the first is open."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        first = stream.sender()
        with pytest.raises(StreamBusyError, match="has a sender in process"):
            stream.sender()
        first.close()
        with pytest.raises(EndOfStream, match="has ended"):
            stream.sender()


def test_a_dropped_sender_leaves_the_stream_open(unique_name: str) -> None:
    """Keep the stream open when the sender handle is dropped, and keep the sender busy."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        stream.sender().send(1)
        assert reader.receive_nowait() == 1
        with pytest.raises(WouldBlock):
            reader.receive_nowait()
        with pytest.raises(StreamBusyError):
            stream.sender()


def test_closing_the_stream_closes_the_held_sender(unique_name: str) -> None:
    """End the stream for readers when the stream closes while its sender is held."""
    stream = SharedStream.create(int, unique_name, capacity=4)
    sender = stream.sender()
    sender.send(1)
    stream.close()
    assert sender.closed
    with pytest.raises(StreamClosedError, match="this end is closed"):
        sender.send_nowait(2)


def test_closing_the_stream_closes_its_ends(unique_name: str) -> None:
    """Raise StreamClosedError from ends of a closed stream."""
    stream = SharedStream.create(int, unique_name, capacity=2)
    reader = stream.reader()
    sender = stream.sender()
    stream.close()
    assert reader.closed and sender.closed
    with pytest.raises(StreamClosedError):
        reader.receive_nowait()
    with pytest.raises(StreamClosedError):
        stream.reader()


def test_attach_with_another_item_type_raises(unique_name: str) -> None:
    """Raise SchemaMismatchError when the item type differs from the creator's."""
    with (
        SharedStream.create(int, unique_name, capacity=2),
        pytest.raises(SchemaMismatchError),
    ):
        SharedStream.attach(float, unique_name)


def test_a_box_cannot_be_an_item(unique_name: str) -> None:
    """Raise TypeError for a SharedBox subclass as the item type."""

    class Motor(SharedBox):
        position: int = 0

    with pytest.raises(TypeError):
        SharedStream.create(Motor, unique_name, capacity=2)


def test_statistics_report_each_reader(unique_name: str) -> None:
    """Report items sent and buffered, and each reader's mode, position and lag."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        lossy = stream.reader(mode="lossy")
        sender = stream.sender()
        for i in range(3):
            sender.send(i)
        lossy.receive_nowait()
        stats = stream.statistics()
        assert (stats.sent, stats.buffered, stats.capacity) == (3, 3, 4)
        assert stats.readers == (ReaderStatistics("lossy", 1, 2, stats.sender_pid),)


def test_a_pickled_stream_and_reader_open_again(unique_name: str) -> None:
    """Unpickle a stream as an attach and a reader as a new reader of the same mode."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        copy = pickle.loads(pickle.dumps(stream))
        assert copy.name == stream.name
        sender = stream.sender()
        sender.send(1)
        reader = pickle.loads(pickle.dumps(stream.reader(mode="latest")))
        assert isinstance(reader, StreamReader)
        assert reader.mode == "latest"
        assert reader.position is None
        assert reader.receive_nowait() == 1
        with pytest.raises(WouldBlock):
            reader.receive_nowait()
        sender.send(2)
        assert reader.receive_nowait() == 2
        with pytest.raises(TypeError, match="SharedStream"):
            pickle.dumps(sender)
        reader.close()
        copy.close()


def test_a_reader_iterates_until_the_stream_ends(unique_name: str) -> None:
    """Stop iteration at EndOfStream rather than raising it."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        reader = stream.reader()
        stream.sender().close()
        assert list(reader) == []
        with pytest.raises(EndOfStream):
            reader.receive_nowait()


def test_threads_receiving_and_closing_at_once(unique_name: str) -> None:
    """Receive from several threads while another closes the reader, without a crash or a lost wake-up."""
    taken: list[list[int]] = [[] for _ in range(4)]
    enough = threading.Event()
    with SharedStream.create(int, unique_name, capacity=16) as stream:
        reader = stream.reader()
        sender = stream.sender()

        def receive(into: list[int]) -> None:
            while True:
                try:
                    into.append(reader.receive(timeout=5))
                except (StreamClosedError, EndOfStream):
                    return
                if sum(map(len, taken)) >= 200:
                    enough.set()

        def send() -> None:
            for i in range(10000):
                sender.send(i, timeout=10)

        threads = [threading.Thread(target=receive, args=(into,)) for into in taken]
        threads.append(threading.Thread(target=send))
        for thread in threads:
            thread.start()
        assert enough.wait(30)
        reader.close()
        for thread in threads:
            thread.join(10)
        assert not any(thread.is_alive() for thread in threads)
    everything = [item for into in taken for item in into]
    assert len(everything) == len(set(everything)) >= 200
    for into in taken:
        assert into == sorted(into)


@pytest.mark.parametrize(
    ("keyword", "allowed"),
    [("mode", "'lossless', 'lossy' or 'latest'"), ("start", "'newest' or 'oldest'")],
)
def test_reader_refuses_an_unknown_mode_or_start(
    unique_name: str, keyword: str, allowed: str
) -> None:
    """Raise ValueError naming the allowed values for an unknown mode or start."""
    unknown: dict[str, Any] = {keyword: "middle"}
    with (
        SharedStream.create(int, unique_name, capacity=2) as stream,
        pytest.raises(ValueError, match=re.escape(allowed)),
    ):
        stream.reader(**unknown)


def test_a_stream_reachable_from_its_item_class_is_collected(unique_name: str) -> None:
    """Free a stream and its ends when its item class refers back to the stream."""

    def build() -> tuple[weakref.ref[Any], weakref.ref[Any]]:
        @dataclass(frozen=True)
        class Local:
            index: int

        stream = SharedStream.create(Local, unique_name, capacity=4)
        Local.stream = stream  # type: ignore[attr-defined]
        Local.sender = stream.sender()  # type: ignore[attr-defined]
        return weakref.ref(stream), weakref.ref(Local)

    stream_ref, class_ref = build()
    gc.collect()
    assert stream_ref() is None
    assert class_ref() is None


LEAK_PROBE = """
import sys
from dataclasses import dataclass

from sharedbox import SharedStream

@dataclass(frozen=True)
class Plain:
    index: int

with SharedStream.create(Plain, sys.argv[1], capacity=8) as stream:
    with stream.sender() as sender:
        sender.send(Plain(1))
SharedStream.unlink(sys.argv[1])
"""


def test_a_stream_of_a_module_level_class_leaves_no_leak_report(
    unique_name: str,
) -> None:
    """Print no nanobind leak report when a script ends holding a stream of a dataclass."""
    done = subprocess.run(
        [sys.executable, "-c", LEAK_PROBE, unique_name],
        capture_output=True,
        check=True,
        text=True,
        timeout=60,
    )
    assert "leaked" not in done.stderr


WORKER_CYCLE_PROBE = """
import asyncio
import gc
import sys
import threading
import weakref
from dataclasses import dataclass

from sharedbox import SharedStream

name = sys.argv[1]


def stream_threads():
    return [t for t in threading.enumerate() if name in t.name]


def build():
    @dataclass(frozen=True)
    class Local:
        index: int

    stream = SharedStream.create(Local, name, capacity=2)
    Local.stream = stream
    Local.reader = stream.reader()
    Local.sender = stream.sender()

    async def use_both_threads():
        Local.sender.send(Local(0))
        Local.sender.send(Local(1))
        blocked = asyncio.create_task(Local.sender.asend(Local(2)))
        while not stream_threads():
            await asyncio.sleep(0)
        got = [(await anext(Local.reader)).index for _ in range(3)]
        await blocked
        waiting = asyncio.create_task(anext(Local.reader))
        while len(stream_threads()) < 2:
            await asyncio.sleep(0)
        Local.sender.send(Local(3))
        got.append((await waiting).index)
        return got

    assert asyncio.run(use_both_threads()) == [0, 1, 2, 3]
    return stream_threads(), [
        weakref.ref(x) for x in (stream, Local, Local.sender, Local.reader)
    ]


threads, refs = build()
assert len(threads) == 2
gc.collect()
assert [r() for r in refs] == [None] * 4
for thread in threads:
    thread.join(5)
assert not any(thread.is_alive() for thread in threads)
SharedStream.unlink(name)
"""


def test_a_stream_whose_ends_started_threads_is_collected_through_its_item_class(
    unique_name: str,
) -> None:
    """Free a stream and its ends, end their threads and print no leak report when the item class refers back to them."""
    done = subprocess.run(
        [sys.executable, "-c", WORKER_CYCLE_PROBE, unique_name],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "leaked" not in done.stderr
