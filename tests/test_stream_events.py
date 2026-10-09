import asyncio
import gc
import multiprocessing as mp
import sys
import threading
import time
from collections.abc import Callable
from multiprocessing.process import BaseProcess
from typing import Annotated, Any

import numpy as np
import psygnal
import pytest
from streamproc import exit_while_delivering, send_ints

from sharedbox import (
    DType,
    EndOfStream,
    ReaderEvents,
    Shape,
    SharedStream,
    StreamClosedError,
    StreamReader,
)
from sharedbox._stream import CountedSignal

Frame = Annotated[np.ndarray, Shape(2), DType("int32")]


def wait_until(
    condition: Callable[[], bool],
    seconds: float = 5.0,
    pump: Callable[[], object] = lambda: None,
) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        pump()
        time.sleep(0.01)


def test_items_from_another_process_are_emitted_in_order(unique_name: str) -> None:
    """Emit received with each item and its position, in order, then ended once."""
    with SharedStream.create(int, unique_name, capacity=8) as stream:
        reader = stream.reader()
        got: list[tuple[int, int]] = []
        ended = threading.Event()
        reader.events.received.connect(
            lambda item, position: got.append((item, position))
        )
        reader.events.ended.connect(ended.set)
        child = mp.get_context("spawn").Process(
            target=send_ints, args=(unique_name, 100)
        )
        child.start()
        child.join(60)
        assert ended.wait(10)
        assert got == [(i, i) for i in range(100)]


def test_callbacks_on_the_main_thread_run_from_emit_queued(unique_name: str) -> None:
    """Run a callback connected with thread="main" only when the main thread calls emit_queued."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        got: list[tuple[int, threading.Thread]] = []
        reader.events.received.connect(
            lambda item, _: got.append((item, threading.current_thread())),
            thread="main",
        )
        stream.sender().send(1)
        time.sleep(0.3)
        assert got == []
        wait_until(lambda: bool(got), pump=psygnal.emit_queued)
        assert got == [(1, threading.main_thread())]


def drain(reader: StreamReader[int]) -> list[int]:
    """Receive with plain calls up to the end of the stream."""
    rest: list[int] = []
    try:
        while True:
            rest.append(reader.receive(timeout=10))
    except EndOfStream:
        pass
    return rest


async def anext_of(reader: StreamReader[int]) -> int:
    return await anext(reader)


@pytest.mark.parametrize(
    "call",
    [
        lambda reader: reader.receive(timeout=0.1),
        lambda reader: reader.receive_nowait(),
        lambda reader: reader.receive_into(None),
        lambda reader: reader.receive_into_nowait(None),
        lambda reader: reader.iter_into(None),
        lambda reader: next(reader),
        lambda reader: reader.receive_future(),
        lambda reader: asyncio.run(anext_of(reader)),
    ],
    ids=[
        "receive",
        "receive_nowait",
        "receive_into",
        "receive_into_nowait",
        "iter_into",
        "next",
        "receive_future",
        "anext",
    ],
)
def test_receiving_raises_while_delivering(
    unique_name: str, call: Callable[[StreamReader[int]], object]
) -> None:
    """Raise RuntimeError from every way of receiving while callbacks are connected."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        reader.events.received.connect(lambda *_: None)
        with pytest.raises(RuntimeError, match="second reader"):
            call(reader)


def test_delivery_stops_when_the_last_callback_disconnects(unique_name: str) -> None:
    """Stop emitting after the last callback disconnects, so receiving works again and gets the next item."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()
        got: list[int] = []

        def keep(item: int, position: int) -> None:
            got.append(item)

        reader.events.connect(lambda info: None)
        reader.events.received.connect(keep)
        sender.send(1)
        wait_until(lambda: got == [1])
        reader.events.received.disconnect(keep)
        reader.events.disconnect()
        sender.send(2)
        assert reader.receive(timeout=5) == 2
        assert got == [1]


def test_async_receive_works_again_after_delivery_stops(unique_name: str) -> None:
    """Receive with anext once delivery stopped."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()
        got: list[int] = []
        reader.events.received.connect(lambda item, _: got.append(item))
        sender.send(1)
        wait_until(lambda: got == [1])
        reader.events.disconnect()
        sender.send(2)
        assert reader.receive_future().result(5) == 2
        sender.send(3)
        assert asyncio.run(anext_of(reader)) == 3


def test_a_callback_can_disconnect_itself(unique_name: str) -> None:
    """Stop delivery when a callback disconnects from inside, and receive the next items afterwards."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()
        got: list[int] = []

        def once(item: int, position: int) -> None:
            reader.events.received.disconnect(once)
            got.append(item)

        reader.events.received.connect(once)
        sender.send(1)
        sender.send(2)
        wait_until(lambda: got == [1])
        assert reader.receive_future().result(5) == 2
        assert got == [1]


def test_close_stops_delivery(unique_name: str) -> None:
    """End the delivering thread when the reader closes."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        reader.events.received.connect(lambda *_: None)
        threads = [t for t in threading.enumerate() if unique_name in t.name]
        assert len(threads) == 1
        reader.close()
        threads[0].join(5)
        assert not threads[0].is_alive()


def disconnect_mid_stream(name: str, count: int) -> list[int]:
    """Return what a callback and then plain receives got from a lossless reader disconnected mid-stream."""
    with SharedStream.create(int, name, capacity=8) as stream:
        reader = stream.reader("lossless")
        sender = stream.sender()
        got: list[int] = []
        reader.events.received.connect(lambda item, _: got.append(item))

        def send() -> None:
            for i in range(count):
                sender.send(i)
            sender.close()

        thread = threading.Thread(target=send)
        thread.start()
        wait_until(lambda: len(got) >= 5)
        reader.events.received.disconnect()
        rest = drain(reader)
        thread.join(10)
        return got + rest


def test_an_item_taken_as_delivery_stops_is_not_lost(unique_name: str) -> None:
    """Hand every item to a callback or to a plain receive, once and in order, when a lossless reader disconnects mid-stream."""
    for run in range(10):
        name = f"{unique_name}-{run}"
        assert disconnect_mid_stream(name, 2000) == list(range(2000))
        SharedStream.unlink(name)


def test_a_collected_callback_stops_delivery_without_the_reader_lock(
    unique_name: str,
) -> None:
    """Stop delivery from a garbage collection that runs while another thread holds the reader's lock."""

    class Owner:
        def __init__(self) -> None:
            self.me = self

        def on(self, item: int, position: int) -> None:
            pass

    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        done = threading.Event()

        def collect_holding_the_lock() -> None:
            with reader._lock:
                gc.collect()
            done.set()

        gc.disable()
        try:
            owner = Owner()
            reader.events.received.connect(owner.on)
            del owner
            threading.Thread(target=collect_holding_the_lock, daemon=True).start()
            assert done.wait(5)
        finally:
            gc.enable()
        stream.sender().send(1)
        assert reader.receive_future().result(5) == 1


def test_a_quick_reconnect_emits_ended_once(unique_name: str) -> None:
    """Emit ended once when the callback disconnects and connects again while delivery starts."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        stream.sender().close()
        ended: list[int] = []

        def on_ended() -> None:
            ended.append(1)

        def on_received(item: int, position: int) -> None:
            pass

        reader.events.ended.connect(on_ended)
        reader.events.received.connect(on_received)
        reader.events.received.disconnect(on_received)
        reader.events.received.connect(on_received)
        wait_until(lambda: bool(ended))
        reader.events.disconnect()
        with pytest.raises(EndOfStream):
            reader.receive_future().result(5)
        assert ended == [1]


def test_ended_alone_does_not_start_delivery(unique_name: str) -> None:
    """Leave the items to receive when only an ended callback is connected."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()
        reader.events.ended.connect(lambda: None)
        sender.send(1)
        assert reader.receive(timeout=5) == 1


def test_an_iterator_made_before_delivery_refuses_while_it_runs(
    unique_name: str,
) -> None:
    """Raise RuntimeError from iter_into iterators, for and async for, once delivery runs."""
    with SharedStream.create(Frame, unique_name, capacity=4) as stream:
        reader = stream.reader()
        iterator = reader.iter_into(np.empty(2, np.int32))
        reader.events.received.connect(lambda *_: None)
        with pytest.raises(RuntimeError, match="second reader"):
            next(iterator)

        async def go() -> None:
            await anext(iterator)

        with pytest.raises(RuntimeError, match="second reader"):
            asyncio.run(go())


def test_an_error_while_receiving_is_logged_and_stops_delivery(
    unique_name: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Log an exception from the receive itself, stop delivering, and let receive work again."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        take = StreamReader._take
        failed = threading.Event()

        def broken(self: StreamReader[int], *args: Any) -> int:
            if threading.current_thread() is not threading.main_thread():
                failed.set()
                raise ValueError("cannot decode")
            return take(self, *args)

        monkeypatch.setattr(StreamReader, "_take", broken)
        reader.events.received.connect(lambda *_: None)
        assert failed.wait(5)
        stream.sender().send(1)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                assert reader.receive(timeout=1) == 1
                break
            except RuntimeError:
                time.sleep(0.01)
        else:
            pytest.fail("receive kept raising RuntimeError")
        assert "stopped on an error" in caplog.text


def test_a_group_without_a_reader_accepts_callbacks() -> None:
    """Connect to a ReaderEvents that no reader owns without raising."""
    events = ReaderEvents()
    events.received.connect(lambda item, position: None)
    assert len(events.received) == 1


def test_a_process_exits_while_delivering(unique_name: str) -> None:
    """Let a process exit with a reader still delivering."""
    with SharedStream.create(int, unique_name, capacity=4):
        child = mp.get_context("spawn").Process(
            target=exit_while_delivering, args=(unique_name,)
        )
        child.start()
        child.join(30)
        assert child.exitcode == 0


def test_a_raising_callback_is_logged_and_delivery_goes_on(
    unique_name: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Log a callback's exception and keep emitting the next items."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        got: list[int] = []

        def fails(item: int, position: int) -> None:
            got.append(item)
            raise ValueError("boom")

        reader.events.received.connect(fails)
        sender = stream.sender()
        sender.send(1)
        sender.send(2)
        wait_until(lambda: got == [1, 2])
        assert got == [1, 2]
        assert "a callback of stream" in caplog.text


def test_psygnal_still_calls_the_slot_hooks() -> None:
    """Fail if psygnal stops calling the slot hooks or the lock that delivery relies on."""
    seen: list[tuple[int, bool]] = []

    class Probe(CountedSignal):
        def _changed(self) -> None:
            owned = self._lock._is_owned()  # type: ignore[attr-defined]
            seen.append((len(self), owned))

    class Group(psygnal.SignalGroup):
        value = psygnal.Signal(int, signal_instance_class=Probe)

    class Owner:
        def __init__(self) -> None:
            self.me = self

        def on(self, value: int) -> None:
            pass

    group = Group()

    def callback(value: int) -> None:
        pass

    group.value.connect(callback)
    group.value.disconnect(callback)
    group.connect(lambda info: None)
    group.disconnect()
    assert seen == [(1, True), (0, True), (1, True), (0, True)]

    owner = Owner()
    group.value.connect(owner.on)
    del owner
    gc.collect()
    assert seen[4:] == [(1, True), (0, False)]


def fork(target: Callable[..., object], *args: object) -> BaseProcess:
    if sys.platform == "win32":
        raise NotImplementedError("Windows has no fork")
    else:
        process = mp.get_context("fork").Process(target=target, args=args, daemon=True)
        process.start()
        return process


def use_inherited_delivering_reader(reader: StreamReader[int]) -> None:
    """Check in a forked child that a reader that was delivering is closed."""
    with pytest.raises(StreamClosedError):
        reader.receive()
    with pytest.raises(StreamClosedError):
        reader.receive_nowait()
    reader.close()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no fork")
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded")
def test_a_forked_child_of_a_delivering_reader_gets_stream_closed_error(
    unique_name: str,
) -> None:
    """Raise StreamClosedError from receive and receive_nowait in the child of a fork."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        reader.events.received.connect(lambda *_: None)
        child = fork(use_inherited_delivering_reader, reader)
        child.join(20)
        assert child.exitcode == 0


def test_an_item_handed_back_does_not_move_the_position(unique_name: str) -> None:
    """Keep reader.position at the last item delivered when delivery hands one back."""
    take = StreamReader._take
    calls: list[int] = []
    positions: list[int | None] = []
    got: list[tuple[int, int]] = []

    def keep(item: int, position: int) -> None:
        got.append((item, position))

    def patched(self: StreamReader[int], *args: Any) -> int:
        calls.append(1)
        if len(calls) == 3:
            positions.append(self.position)
        item = take(self, *args)
        if len(calls) == 2:
            self.events.received.disconnect(keep)
        return item

    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        sender = stream.sender()
        sender.send(10)
        sender.send(11)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(StreamReader, "_take", patched)
            reader.events.received.connect(keep)
            deadline = time.monotonic() + 5
            while True:
                try:
                    received = reader.receive(timeout=5)
                    break
                except RuntimeError:
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
        assert got == [(10, 0)]
        assert positions == [0]
        assert received == 11
        assert reader.position == 1
