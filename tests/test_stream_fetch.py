import asyncio
import multiprocessing as mp
import threading
import time
from multiprocessing.queues import Queue

import numpy as np
import pytest
from streamproc import Frame, fetch_frames, finish, frame

from sharedbox import EndOfStream, ItemGoneError, SharedStream

CONTEXT = mp.get_context("spawn")


def test_a_worker_fetches_frames_the_parent_is_still_sending(unique_name: str) -> None:
    """Fetch frames 120 to 135 in another process while the parent is still sending them."""
    with SharedStream.create(Frame, unique_name, capacity=64) as stream:
        sender = stream.sender()
        results: Queue[list[int]] = CONTEXT.Queue()
        ready = CONTEXT.Event()
        child = CONTEXT.Process(
            target=fetch_frames, args=(unique_name, 120, 135, results, ready)
        )
        child.start()
        assert ready.wait(60)
        for i in range(140):
            sender.send(frame(i), timeout=60)
        assert results.get(timeout=60) == list(range(120, 136))
        finish(child)
    SharedStream.unlink(unique_name)


def test_fetch_leaves_the_cursor_alone(unique_name: str) -> None:
    """Return the fetched item and set position to it, while the next receive returns the cursor's item."""
    with SharedStream.create(int, unique_name, capacity=8) as stream:
        reader = stream.reader("lossless", start="oldest")
        sender = stream.sender()
        for i in range(5):
            sender.send(i)
        assert reader.fetch(3) == 3
        assert reader.position == 3
        assert reader.receive(timeout=5) == 0
        assert reader.missed == 0
    SharedStream.unlink(unique_name)


def test_fetch_into_fills_the_given_array(unique_name: str) -> None:
    """Read the fetched item's array into the array the caller passed."""
    with SharedStream.create(Frame, unique_name, capacity=4) as stream:
        reader = stream.reader("lossy", start="oldest")
        sender = stream.sender()
        for i in range(3):
            sender.send(frame(i))
        image = np.empty((64, 64), np.uint16)
        item = reader.fetch_into(2, {"image": image}, timeout=5)
        assert item.image is image
        assert int(image[0, 0]) == 2
    SharedStream.unlink(unique_name)


def test_fetch_future_under_asyncio(unique_name: str) -> None:
    """Resolve a fetch future once the sender sends the item it names."""
    with SharedStream.create(int, unique_name, capacity=8) as stream:
        reader = stream.reader("lossy", start="oldest")
        sender = stream.sender()

        def send_later() -> None:
            time.sleep(0.1)
            for i in range(6):
                sender.send(i)

        async def fetch() -> int:
            return await asyncio.wrap_future(reader.fetch_future(5))

        thread = threading.Thread(target=send_later)
        thread.start()
        assert asyncio.run(fetch()) == 5
        thread.join()
    SharedStream.unlink(unique_name)


def test_an_abandoned_fetch_future_hands_nothing_to_receive(unique_name: str) -> None:
    """Return the cursor's item from receive after the awaiter of a fetch future gave up."""
    with SharedStream.create(int, unique_name, capacity=8) as stream:
        reader = stream.reader("lossy", start="oldest")
        sender = stream.sender()
        sender.send(0)

        async def give_up() -> None:
            await asyncio.wait_for(asyncio.wrap_future(reader.fetch_future(3)), 0.2)

        with pytest.raises(TimeoutError):
            asyncio.run(give_up())
        for i in range(1, 4):
            sender.send(i)
        assert reader.receive(timeout=5) == 0
    SharedStream.unlink(unique_name)


def test_an_overwritten_item_raises_item_gone(unique_name: str) -> None:
    """Raise ItemGoneError, a LookupError, with the oldest position the ring held."""
    with SharedStream.create(int, unique_name, capacity=2) as stream:
        reader = stream.reader("lossy", start="oldest")
        sender = stream.sender()
        for i in range(5):
            sender.send(i)
        with pytest.raises(ItemGoneError) as excinfo:
            reader.fetch(1, timeout=0)
        assert excinfo.value.oldest == 3
        assert isinstance(excinfo.value, LookupError)
    SharedStream.unlink(unique_name)


def test_metadata_after_fetch_describes_the_fetched_item(unique_name: str) -> None:
    """Report the metadata version of the item fetched last."""
    with SharedStream.create(
        int, unique_name, capacity=8, metadata={"step": 1}
    ) as stream:
        reader = stream.reader("lossy", start="oldest")
        sender = stream.sender()
        sender.send(0)
        sender.send(1, metadata={"step": 2})
        reader.fetch(0)
        assert reader.metadata_version == 1
        reader.fetch(1)
        assert reader.metadata_version == 2
        assert reader.metadata == {"step": 2}
    SharedStream.unlink(unique_name)


def test_fetch_past_the_end_raises_end_of_stream(unique_name: str) -> None:
    """Raise EndOfStream for a position the closed sender never sent."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader("lossy", start="oldest")
        with stream.sender() as sender:
            sender.send(0)
        with pytest.raises(EndOfStream):
            reader.fetch(1, timeout=5)
    SharedStream.unlink(unique_name)


def test_fetch_times_out(unique_name: str) -> None:
    """Raise TimeoutError at once for an item not sent yet with a timeout of 0."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        with pytest.raises(TimeoutError):
            reader.fetch(0, timeout=0)
    SharedStream.unlink(unique_name)


@pytest.mark.parametrize("position", [-1, 2**63 - 1])
def test_fetch_refuses_a_position_out_of_range(unique_name: str, position: int) -> None:
    """Raise ValueError for a negative position and for one whose sequence number would wrap."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        with pytest.raises(ValueError):
            reader.fetch(position, timeout=0)
        with pytest.raises(ValueError):
            reader.fetch_future(position)
    SharedStream.unlink(unique_name)


def test_fetch_refuses_while_delivering(unique_name: str) -> None:
    """Raise RuntimeError from every fetch while callbacks are connected."""
    with SharedStream.create(int, unique_name, capacity=4) as stream:
        reader = stream.reader()
        reader.events.received.connect(lambda *_: None)
        with pytest.raises(RuntimeError, match="second reader"):
            reader.fetch(0)
        with pytest.raises(RuntimeError, match="second reader"):
            reader.fetch_future(0)
    SharedStream.unlink(unique_name)


def test_cancelling_a_fetch_future_frees_the_reader(unique_name: str) -> None:
    """Stop a waiting fetch when its future is cancelled, so later calls on the reader do not wait for it."""
    with SharedStream.create(int, unique_name, capacity=8) as stream:
        reader = stream.reader("lossy", start="oldest")
        sender = stream.sender()
        sender.send(0)

        async def give_up() -> None:
            await asyncio.wait_for(asyncio.wrap_future(reader.fetch_future(100)), 0.2)

        with pytest.raises(TimeoutError):
            asyncio.run(give_up())
        assert reader.receive(timeout=2) == 0
        assert reader.fetch(0, timeout=2) == 0
    SharedStream.unlink(unique_name)
