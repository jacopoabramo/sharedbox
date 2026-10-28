import ctypes
import gc
import pickle
import re
import subprocess
import sys
import threading
import time
import weakref
from dataclasses import dataclass, field, replace
from typing import Annotated, Any, NotRequired, TypedDict

import numpy as np
import numpy.typing as npt
import pytest

from sharedbox import (
    Capacity,
    DType,
    EndOfStream,
    MaxShape,
    ReaderReleased,
    ReceiveStreamStatistics,
    SchemaMismatchError,
    Shape,
    SharedBox,
    SharedSendStream,
    SharedStream,
    StreamBusyError,
    StreamClosedError,
    WouldBlock,
)


@dataclass(frozen=True)
class Frame:
    index: int
    image: Annotated[np.ndarray, Shape((4, 4)), DType("uint16")]


def frame(i: int) -> Frame:
    return Frame(i, np.full((4, 4), i, np.uint16))


@dataclass(frozen=True)
class Mixed:
    note: int | None
    values: Annotated[list[int], Capacity(4)]


@dataclass(frozen=True)
class Block:
    run: int
    counts: Annotated[npt.NDArray[np.uint32], MaxShape((4, 3))]


Counts = Annotated[npt.NDArray[np.uint32], MaxShape((4, 3))]


def counts(*shape: int) -> npt.NDArray[np.uint32]:
    return np.arange(int(np.prod(shape)), dtype=np.uint32).reshape(shape)


class Entry(TypedDict):
    index: int
    extra: NotRequired[int]


def test_items_go_from_the_sender_to_a_reader(unique_name: str) -> None:
    """Receive every item sent, in order, then EndOfStream after the sender closes."""
    with SharedStream.create(Frame, unique_name, capacity=4) as stream:
        reader = stream.reader()
        with stream.sender() as sender:
            for i in range(3):
                sender.send(frame(i))
        assert [item.index for item in reader] == [0, 1, 2]
        assert reader.position == 2


@dataclass
class Notice:
    index: int
    scan: Annotated[npt.NDArray[np.uint8], Shape((4,))] | None = None


def test_an_optional_array_member_crosses_a_stream(unique_name: str) -> None:
    """Receive a Notice with no scan and one with a scan, as sent."""
    with SharedStream.create(Notice, unique_name, capacity=4) as stream:
        reader = stream.reader()
        with stream.sender() as sender:
            sender.send(Notice(0))
            sender.send(Notice(1, np.arange(4, dtype=np.uint8)))
        empty, full = list(reader)
        assert (empty.index, empty.scan) == (0, None)
        assert full.index == 1
        assert full.scan is not None
        assert np.array_equal(full.scan, np.arange(4, dtype=np.uint8))


@dataclass
class Letter:
    index: int
    scan: npt.NDArray[np.uint8] = field(metadata={"shape": (32, 32)})


def test_a_dataclass_member_takes_its_array_sizes_from_metadata(
    unique_name: str,
) -> None:
    """Send a Letter whose scan sizes come from the dataclass field's metadata."""
    with SharedStream.create(Letter, unique_name, capacity=2) as stream:
        reader = stream.reader()
        with stream.sender() as sender:
            sender.send(Letter(1, np.full((32, 32), 7, np.uint8)))
        (got,) = list(reader)
        assert got.index == 1
        assert np.array_equal(got.scan, np.full((32, 32), 7, np.uint8))


def test_receive_into_refuses_an_optional_array(unique_name: str) -> None:
    """Raise TypeError when receive_into is given an array for an optional array member."""
    with SharedStream.create(Notice, unique_name, capacity=4) as stream:
        reader = stream.reader()
        stream.sender().send(Notice(1, np.arange(4, dtype=np.uint8)))
        with pytest.raises(TypeError, match="is an optional member"):
            reader.receive_into({"scan": np.zeros(4, np.uint8)})


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
            Shape((3,)),
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
        Shape((2,)),
        DType("uint8"),
    ]


@dataclass(frozen=True)
class Outer:
    inner: Inner
    pair: tuple[
        Annotated[
            np.ndarray,
            Shape((2,)),
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
        image: Annotated[np.ndarray, Shape((2,)), DType("uint8")]

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
        assert stats.readers == (
            ReceiveStreamStatistics("lossy", 1, 2, stats.sender_pid),
        )


def test_a_pickled_stream_attaches_and_its_ends_refuse(unique_name: str) -> None:
    """Unpickle a stream as an attach, and refuse to pickle its sender or a reader."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        copy = pickle.loads(pickle.dumps(stream))
        assert copy.name == stream.name
        reader = copy.reader()
        sender = stream.sender()
        sender.send(1)
        assert reader.receive_nowait() == 1
        with pytest.raises(TypeError, match=r"call sender\(\)"):
            pickle.dumps(sender)
        with pytest.raises(TypeError, match=r"call reader\(\)"):
            pickle.dumps(reader)
        copy.close()


def test_a_pickle_of_a_stream_made_again_is_refused(unique_name: str) -> None:
    """Raise SchemaMismatchError for a pickle taken before the stream was created again."""
    stream = SharedStream.create(int, unique_name, capacity=2)
    data = pickle.dumps(stream)
    stream.close()
    SharedStream.unlink(unique_name)
    with (
        SharedStream.create(int, unique_name, capacity=2),
        pytest.raises(SchemaMismatchError, match="pickled from a different stream"),
    ):
        pickle.loads(data)


def test_a_stream_without_a_name_gets_one_its_ends_lead_back_to() -> None:
    """Name a stream created without a name and reach it from its ends."""
    with SharedStream.create(int, capacity=2) as stream:
        try:
            assert re.fullmatch(r"[0-9a-f]{16}", stream.name)
            sender = stream.sender()
            assert sender.stream is stream
            other = SharedStream.attach(int, stream.name)
            reader = other.reader()
            sender.send(1)
            assert reader.receive_nowait() == 1
            assert reader.stream.statistics().sent == 1
            other.close()
        finally:
            SharedStream.unlink(stream.name)


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
import ctypes
import gc
import sys
import threading
import time
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
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    gc.collect()
    if all(r() is None for r in refs):
        break
    time.sleep(0.01)
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


@pytest.mark.parametrize(
    ("hint", "older", "newer"),
    [
        (Mixed, Mixed(5, [1, 2, 3]), Mixed(None, [9])),
        (Entry, Entry(index=0, extra=1), Entry(index=2)),
    ],
    ids=["optional-and-list", "typeddict"],
)
def test_an_item_sent_over_an_older_one_keeps_nothing_of_it(
    unique_name: str, hint: Any, older: Any, newer: Any
) -> None:
    """Receive a None, a missing key or a shorter list as sent, though the slot held an item with them present."""
    with SharedStream.create(hint, unique_name, capacity=2) as stream:
        reader = stream.reader(start="oldest")
        sender = stream.sender()
        sender.send(older)
        sender.send(older)
        assert [reader.receive(timeout=5), reader.receive(timeout=5)] == [older, older]
        sender.send(newer)
        assert reader.receive(timeout=5) == newer


def test_an_item_that_fails_to_encode_is_not_sent(unique_name: str) -> None:
    """Raise for an item of the wrong shape, send nothing, and give its position to the next item."""
    with SharedStream.create(Frame, unique_name, capacity=2) as stream:
        reader = stream.reader()
        sender = stream.sender()
        with pytest.raises(ValueError, match="Shape"):
            sender.send(Frame(0, np.zeros((2, 2), np.uint16)))
        assert stream.statistics().sent == 0
        sender.send(frame(1))
        assert reader.receive(timeout=5).index == 1
        assert reader.position == 0


def test_a_max_shape_array_crosses_a_stream_in_its_own_shape(unique_name: str) -> None:
    """Receive each MaxShape array, of a record member or as the item, in the shape it was sent in."""
    shapes = [(4, 3), (1, 3), (2, 1), (0, 3)]
    with SharedStream.create(Block, unique_name, capacity=8) as stream:
        reader = stream.reader()
        with stream.sender() as sender:
            for i, shape in enumerate(shapes):
                sender.send(Block(i, counts(*shape)))
        for i, shape in enumerate(shapes):
            item = reader.receive(timeout=5)
            assert item.run == i
            assert item.counts.dtype == np.uint32
            assert item.counts.shape == shape
            assert np.array_equal(item.counts, counts(*shape))
    with SharedStream.create(Counts, f"{unique_name}-bare", capacity=2) as stream:
        bare = stream.reader()
        with stream.sender() as sender:
            sender.send(counts(3, 2))
        got = bare.receive(timeout=5)
        assert got.shape == (3, 2)
        assert np.array_equal(got, counts(3, 2))


def test_send_gathers_a_strided_max_shape_array(unique_name: str) -> None:
    """Receive a strided array sent as a MaxShape array with the same values."""
    strided = np.arange(24, dtype=np.uint32).reshape(4, 6)[:, ::2]
    with SharedStream.create(Block, unique_name, capacity=2) as stream:
        reader = stream.reader()
        with stream.sender() as sender:
            sender.send(Block(0, strided))
        assert np.array_equal(reader.receive(timeout=5).counts, strided)


@pytest.mark.parametrize("shape", [(5, 3), (4, 4), (4,), (4, 3, 1)])
def test_send_refuses_a_shape_beyond_the_maximum(
    unique_name: str, shape: tuple[int, ...]
) -> None:
    """Refuse a MaxShape array of a larger dimension or another dimension count, and send nothing."""
    with SharedStream.create(Block, unique_name, capacity=2) as stream:
        reader = stream.reader()
        with stream.sender() as sender:
            with pytest.raises(ValueError, match="MaxShape"):
                sender.send(Block(0, np.zeros(shape, np.uint32)))
            sender.send(Block(1, counts(2, 3)))
        assert reader.receive(timeout=5).run == 1


@dataclass(frozen=True)
class MetadataMaxShape:
    counts: npt.NDArray[np.uint32] = field(metadata={"max_shape": (4, 3)})


def define_box_field() -> None:
    class Held(SharedBox):
        counts: Counts


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(define_box_field, id="box field"),
        pytest.param(
            lambda name: SharedStream.create(Counts | None, name, capacity=2),
            id="optional",
        ),
        pytest.param(
            lambda name: SharedStream.create(
                Annotated[list[Block], Capacity(2)], name, capacity=2
            ),
            id="list element",
        ),
        pytest.param(
            lambda name: SharedStream.create(Counts | int, name, capacity=2), id="union"
        ),
        pytest.param(
            lambda name: SharedStream.create(
                Annotated[npt.NDArray[np.uint32], Shape((4, 3)), MaxShape((4, 3))],
                name,
                capacity=2,
            ),
            id="with Shape",
        ),
        pytest.param(
            lambda name: SharedStream.create(MetadataMaxShape, name, capacity=2),
            id="metadata",
        ),
    ],
)
def test_max_shape_is_refused_outside_stream_items(unique_name: str, make: Any) -> None:
    """Refuse MaxShape on a box field, under an optional, union or list, with Shape, and as metadata."""
    with pytest.raises(TypeError, match="MaxShape|max_shape|union member"):
        if make is define_box_field:
            make()
        else:
            make(unique_name)


@pytest.mark.parametrize("dims", [(), (0,), (2,) * 9, (True,), 4, [2, 3]])
def test_max_shape_refuses_bad_dimensions(dims: tuple[Any, ...]) -> None:
    """Refuse a MaxShape without 1 to 8 integer dimensions of at least 1."""
    with pytest.raises(ValueError):
        MaxShape(dims)


@dataclass(frozen=True)
class Twin:
    first: Annotated[npt.NDArray[np.uint32], MaxShape((4, 3))]
    run: int
    second: Annotated[npt.NDArray[np.uint32], MaxShape((4, 3))]


def test_writing_views_the_slot_in_the_template_shape(unique_name: str) -> None:
    """Give the slot's array the template array's shape and send what was written into it."""
    with SharedStream.create(Block, unique_name, capacity=2) as stream:
        reader = stream.reader()
        template = np.empty((2, 3), np.uint32)
        with stream.sender().writing(Block(1, template)) as slot:
            assert slot.item.counts.shape == (2, 3)
            assert not np.shares_memory(slot.item.counts, template)
            slot.item.counts[:] = [[1, 2, 3], [4, 5, 6]]
            slot.send(slot.item)
        got = reader.receive(timeout=5)
        np.testing.assert_array_equal(got.counts, [[1, 2, 3], [4, 5, 6]])


def test_slot_send_publishes_a_prefix_of_the_slot_view(unique_name: str) -> None:
    """Send the first rows of the slot's array as an item of that shape."""
    with SharedStream.create(Block, unique_name, capacity=2) as stream:
        reader = stream.reader()
        with stream.sender().writing(Block(1, np.empty((4, 3), np.uint32))) as slot:
            slot.item.counts[:] = counts(4, 3)
            slot.send(replace(slot.item, counts=slot.item.counts[:2]))
        got = reader.receive(timeout=5)
        assert got.counts.shape == (2, 3)
        np.testing.assert_array_equal(got.counts, counts(4, 3)[:2])


def test_slot_send_copies_a_view_not_at_the_start_of_the_slot(unique_name: str) -> None:
    """Send rows from the middle and a column slice of the slot's array with the values they held."""
    with SharedStream.create(Block, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()
        for index in (np.s_[1:3], np.s_[:, :2]):
            with sender.writing(Block(1, np.empty((4, 3), np.uint32))) as slot:
                slot.item.counts[:] = counts(4, 3)
                chosen = slot.item.counts[index]
                expected = chosen.copy()
                slot.send(replace(slot.item, counts=chosen))
            got = reader.receive(timeout=5)
            assert got.counts.shape == expected.shape
            np.testing.assert_array_equal(got.counts, expected)


def test_writing_without_a_template_views_the_maximum(unique_name: str) -> None:
    """Give a bare MaxShape item's slot array the maximum shape when writing takes no template."""
    with (
        SharedStream.create(Counts, unique_name, capacity=2) as stream,
        stream.sender().writing() as slot,
    ):
        assert slot.item.shape == (4, 3)


def test_writing_refuses_a_template_beyond_the_maximum(unique_name: str) -> None:
    """Raise ValueError for a template array beyond MaxShape and still send afterwards."""
    with SharedStream.create(Block, unique_name, capacity=2) as stream:
        reader = stream.reader()
        sender = stream.sender()
        with (
            pytest.raises(ValueError, match="MaxShape"),
            sender.writing(Block(1, np.empty((5, 3), np.uint32)), timeout=5),
        ):
            pass
        sender.send(Block(2, counts(1, 3)), timeout=5)
        assert reader.receive(timeout=5).run == 2


def test_receive_into_returns_a_view_of_the_stored_shape(unique_name: str) -> None:
    """Return a view over out in the stored shape, its elements packed at the start of out."""
    with SharedStream.create(Block, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()
        sender.send(Block(1, counts(2, 3)))
        sender.send(Block(2, counts(0, 3)))
        out = np.zeros((4, 3), np.uint32)
        item = reader.receive_into({"counts": out}, timeout=5)
        assert item.counts.shape == (2, 3)
        assert np.shares_memory(item.counts, out)
        np.testing.assert_array_equal(out.ravel()[:6], np.arange(6))
        assert reader.receive_into({"counts": out}, timeout=5).counts.shape == (0, 3)


def test_receive_into_reads_two_max_shape_members(unique_name: str) -> None:
    """Read both MaxShape members of an item in their own shapes when out is given for one."""
    with SharedStream.create(Twin, unique_name, capacity=2) as stream:
        reader = stream.reader()
        stream.sender().send(Twin(counts(3, 3), 7, counts(1, 2)))
        out = np.zeros((4, 3), np.uint32)
        item = reader.receive_into({"first": out}, timeout=5)
        np.testing.assert_array_equal(item.first, counts(3, 3))
        np.testing.assert_array_equal(item.second, counts(1, 2))
        assert item.run == 7


def test_receive_into_refuses_an_out_not_of_the_maximum_shape(unique_name: str) -> None:
    """Raise ValueError for an out of another shape than MaxShape and leave the item for the next receive."""
    with SharedStream.create(Block, unique_name, capacity=2) as stream:
        reader = stream.reader()
        stream.sender().send(Block(1, counts(2, 3)))
        with pytest.raises(ValueError, match="shape"):
            reader.receive_into({"counts": np.zeros((2, 3), np.uint32)}, timeout=5)
        assert reader.receive(timeout=5).run == 1


class Tally(TypedDict):
    run: int
    counts: Counts


def test_writing_takes_a_typed_dict_template_without_its_max_shape_key(
    unique_name: str,
) -> None:
    """Give a TypedDict template's missing MaxShape key the maximum shape over the slot."""
    with SharedStream.create(Tally, unique_name, capacity=2) as stream:
        reader = stream.reader()
        template: Any = {"run": 1}
        with stream.sender().writing(template) as slot:
            assert slot.item["counts"].shape == (4, 3)
            slot.item["counts"][:] = counts(4, 3)
            slot.send(slot.item)
        np.testing.assert_array_equal(reader.receive(timeout=5)["counts"], counts(4, 3))


def test_a_stored_shape_beyond_the_maximum_is_refused(unique_name: str) -> None:
    """Raise ValueError for a slot whose shape line exceeds MaxShape, write nothing to out, and read the next item."""
    with SharedStream.create(Block, unique_name, capacity=4) as stream:
        plain = stream.reader()
        into = stream.reader()
        sender = stream.sender()
        with sender.writing(Block(1, np.empty((2, 3), np.uint32))) as slot:
            view = slot.item.counts
            slot.send(slot.item)
        line = (ctypes.c_uint64 * 8).from_address(view.ctypes.data - 64)
        line[0] = 5
        sender.send(Block(2, counts(1, 3)))
        out = np.zeros((4, 3), np.uint32)
        with pytest.raises(ValueError, match="stored value"):
            plain.receive(timeout=5)
        with pytest.raises(ValueError, match="stored value"):
            into.receive_into({"counts": out}, timeout=5)
        assert not out.any()
        assert plain.receive(timeout=5).run == 2
        assert into.receive_into({"counts": out}, timeout=5).run == 2


def fill(sender: SharedSendStream[int], count: int) -> None:
    """Send the integers below `count`."""
    for i in range(count):
        sender.send(i, timeout=5)


def test_a_release_lets_the_sender_go_on_and_the_reader_resume(
    unique_name: str,
) -> None:
    """Release a lossless reader a full ring behind, then receive from the newest item."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender(on_full="release")
        fill(sender, 4)
        sender.send(4, timeout=0.1)
        with pytest.raises(ReaderReleased) as released:
            reader.receive(timeout=1)
        assert released.value.missed == 4
        assert reader.missed == 4
        assert reader.receive(timeout=1) == 4
        sender.send(5)
        assert reader.receive(timeout=1) == 5


def test_the_wait_policy_keeps_timeout_error(unique_name: str) -> None:
    """Raise TimeoutError and release nothing under the default policy."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()
        fill(sender, 4)
        with pytest.raises(TimeoutError):
            sender.send(4, timeout=0.1)
        assert reader.receive(timeout=1) == 0
        assert reader.missed == 0


def test_send_nowait_releases_at_once(unique_name: str) -> None:
    """Send without raising WouldBlock under release and tell the reader."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender(on_full="release")
        fill(sender, 4)
        sender.send_nowait(4)
        with pytest.raises(ReaderReleased):
            reader.receive_nowait()
        assert reader.receive_nowait() == 4


def test_writing_with_a_timeout_releases(unique_name: str) -> None:
    """Reserve a slot after a release timeout and tell the reader."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender(on_full="release")
        fill(sender, 4)
        with sender.writing(0, timeout=0.1) as slot:
            slot.send(4)
        with pytest.raises(ReaderReleased):
            reader.receive(timeout=1)
        assert reader.receive(timeout=1) == 4


def test_a_release_waits_the_whole_timeout(unique_name: str) -> None:
    """Wait the full timeout, across several native steps, before releasing."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender(on_full="release")
        fill(sender, 4)
        start = time.monotonic()
        sender.send(4, timeout=1.3)
        assert time.monotonic() - start >= 1.3
        with pytest.raises(ReaderReleased):
            reader.receive(timeout=1)


def test_a_held_writing_slot_is_not_released(unique_name: str) -> None:
    """Raise TimeoutError and WouldBlock while another thread holds the slot, and release nobody."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender(on_full="release")
        fill(sender, 3)
        holding, done = threading.Event(), threading.Event()

        def hold() -> None:
            with sender.writing(0):
                holding.set()
                done.wait(10)

        thread = threading.Thread(target=hold)
        thread.start()
        try:
            assert holding.wait(5)
            with pytest.raises(TimeoutError):
                sender.send(9, timeout=0.2)
            with pytest.raises(WouldBlock):
                sender.send_nowait(9)
        finally:
            done.set()
            thread.join()
        assert [reader.receive(timeout=1) for _ in range(3)] == [0, 1, 2]
        assert reader.missed == 0


def test_sender_refuses_an_unknown_policy(unique_name: str) -> None:
    """Raise ValueError for a policy other than wait and release."""
    with (
        SharedStream.create(int, unique_name, capacity=4) as stream,
        pytest.raises(ValueError, match="drop"),
    ):
        stream.sender(on_full="drop")  # type: ignore[arg-type]
