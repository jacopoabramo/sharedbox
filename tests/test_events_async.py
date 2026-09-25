import asyncio
import multiprocessing as mp
import queue
import threading
import time

import pytest

from sharedbox import FieldWatch, SharedBox


class Counter(SharedBox):
    value: int = 0


def set_value_later(name: str, value: int, delay: float) -> None:
    time.sleep(delay)
    box = Counter.attach(name)
    box.value = value
    box.close()


async def first(watch: FieldWatch[int]) -> int:
    async for value in watch:
        return value
    raise AssertionError("the watch ended without a value")


def test_async_watch_sees_write_from_other_process(unique_name: str) -> None:
    async def main() -> int:
        with Counter.create(unique_name) as box:
            watch = box.watch("value")
            writer = mp.get_context("spawn").Process(
                target=set_value_later, args=(unique_name, 5, 0.2)
            )
            writer.start()
            value = await asyncio.wait_for(first(watch), 20)
            writer.join()
            return value

    assert asyncio.run(main()) == 5


def test_async_watch_skips_to_the_latest_value(unique_name: str) -> None:
    async def main() -> int:
        with Counter.create(unique_name) as box:
            watch = box.watch("value")
            box.value = 1
            box.value = 2
            return await asyncio.wait_for(first(watch), 5)

    assert asyncio.run(main()) == 2


def test_async_watch_follows_writes(unique_name: str) -> None:
    async def main() -> list[int]:
        with Counter.create(unique_name) as box:
            watch = box.watch("value")

            async def write() -> None:
                for i in range(1, 4):
                    await asyncio.sleep(0.05)
                    box.value = i

            writer = asyncio.create_task(write())
            seen: list[int] = []
            async for value in watch:
                seen.append(value)
                if value == 3:
                    break
            await writer
            return seen

    seen = asyncio.run(main())
    assert seen[-1] == 3
    assert seen == sorted(seen)


def test_cancelled_consumer_leaves_the_box_usable(unique_name: str) -> None:
    async def main() -> int:
        with Counter.create(unique_name) as box:
            consumer = asyncio.create_task(first(box.watch("value")))
            await asyncio.sleep(0.05)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            watch = box.watch("value")
            asyncio.get_running_loop().call_later(0.05, setattr, box, "value", 6)
            return await asyncio.wait_for(first(watch), 5)

    assert asyncio.run(main()) == 6


def test_async_watch_ends_on_close(unique_name: str) -> None:
    async def main() -> list[int]:
        box = Counter.create(unique_name)
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, setattr, box, "value", 1)
        loop.call_later(0.3, box.close)
        return [value async for value in box.watch("value")]

    assert asyncio.run(main()) == [1]


class Guarded(SharedBox, lock_timeout=0.2):
    value: int = 0
    other: int = 0


def test_watcher_recovers_after_a_lock_timeout(
    unique_name: str, caplog: pytest.LogCaptureFixture
) -> None:
    seen: queue.Queue[int] = queue.Queue()
    entered, release = threading.Event(), threading.Event()

    def block(new: int) -> None:
        entered.set()
        release.wait(5)

    async def main() -> int:
        with Guarded.create(unique_name) as box:
            box.events.other.connect(block)
            box.events.value.connect(seen.put)
            pending = asyncio.ensure_future(anext(aiter(box.watch("value"))))
            await asyncio.sleep(0)
            box.other = 1
            assert entered.wait(5)
            box.value = 2
            box._segment._hold_write_lock()
            release.set()
            await asyncio.sleep(0.5)
            box.force_unlock()
            return await asyncio.wait_for(pending, 5)

    assert asyncio.run(main()) == 2
    assert seen.get(timeout=5) == 2
    assert caplog.text.count("locked") == 1


def test_cancelling_the_consumer_while_the_box_closes_raises(unique_name: str) -> None:
    async def main() -> None:
        box = Counter.create(unique_name)
        consumer = asyncio.ensure_future(first(box.watch("value")))
        await asyncio.sleep(0)
        closer = threading.Thread(target=box.close)
        closer.start()
        closer.join()
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer

    asyncio.run(main())


def test_close_from_another_thread_ends_async_iteration(unique_name: str) -> None:
    async def main() -> list[int]:
        box = Counter.create(unique_name)
        threading.Timer(0.2, box.close).start()
        return [value async for value in box.watch("value")]

    assert asyncio.run(asyncio.wait_for(main(), 5)) == []


def test_write_just_before_close_is_delivered(unique_name: str) -> None:
    seen: queue.Queue[int] = queue.Queue()

    async def main() -> int:
        box = Counter.create(unique_name)
        box.events.value.connect(seen.put)
        pending = asyncio.ensure_future(anext(aiter(box.watch("value"))))
        await asyncio.sleep(0)
        box.value = 1
        box.close()
        return await asyncio.wait_for(pending, 5)

    assert asyncio.run(main()) == 1
    assert seen.get(timeout=1) == 1
