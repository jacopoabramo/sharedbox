import multiprocessing as mp
import threading
import time
from collections.abc import Callable

import psygnal
import pytest
from psygnal import SignalInstance
from streamproc import send_ints

from sharedbox import SharedStream
from sharedbox._stream import CountedSignal


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


def test_receive_raises_while_delivering(unique_name: str) -> None:
    """Raise RuntimeError from receive, iteration and receive_future while callbacks are connected."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        reader.events.received.connect(lambda *_: None)
        for call in (
            reader.receive_nowait,
            lambda: next(iter(reader)),
            reader.receive_future,
        ):
            with pytest.raises(RuntimeError, match="second reader"):
                call()


def test_delivery_stops_when_the_last_callback_disconnects(unique_name: str) -> None:
    """Stop emitting after the last callback disconnects, so receive works again and gets the next item."""
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
        wait_until(lambda: not reader._delivering)
        sender.send(2)
        assert reader.receive(timeout=5) == 2
        assert got == [1]


def test_close_stops_delivery(unique_name: str) -> None:
    """End the delivering thread when the reader closes."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        reader.events.received.connect(lambda *_: None)
        assert reader._worker is not None
        thread = reader._worker.thread
        reader.close()
        thread.join(5)
        assert not thread.is_alive()


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
    """Fail if psygnal stops calling SignalInstance._append_slot and _remove_slot, which delivery relies on."""
    assert issubclass(CountedSignal, SignalInstance)
    seen: list[int] = []

    class Probe(CountedSignal):
        def _changed(self) -> None:
            seen.append(len(self))

    class Group(psygnal.SignalGroup):
        value = psygnal.Signal(int, signal_instance_class=Probe)

    group = Group()

    def callback(value: int) -> None:
        pass

    group.value.connect(callback)
    group.value.disconnect(callback)
    group.connect(lambda info: None)
    group.disconnect()
    assert seen == [1, 0, 1, 0]
