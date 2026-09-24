import asyncio
import multiprocessing as mp
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
            writer = mp.get_context("spawn").Process(target=set_value_later, args=(unique_name, 5, 0.2))
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
