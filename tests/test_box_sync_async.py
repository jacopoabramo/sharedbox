import asyncio
import multiprocessing as mp
import threading

from sharedbox import SharedBox


class Counter(SharedBox):
    value: int = 0


def count_up(name: str, last: int, go: "mp.synchronize.Event") -> None:
    go.wait(60)
    box = Counter.attach(name)
    for value in range(1, last + 1):
        box.value = value
    box.close()


def test_a_sync_and_an_async_watch_in_two_threads_both_see_another_process(
    unique_name: str,
) -> None:
    """Show the last value written by another process to a sync watch and an async watch of one field, each in its own thread."""
    last = 50
    context = mp.get_context("spawn")
    go = context.Event()
    with Counter.create(unique_name) as box:
        sync_watch, async_watch = box.watch("value"), box.watch("value")
        seen: dict[str, list[int]] = {"sync": [], "async": []}

        def watch_sync() -> None:
            for value in sync_watch:
                seen["sync"].append(value)
                if value == last:
                    return

        def watch_async() -> None:
            async def run() -> None:
                async for value in async_watch:
                    seen["async"].append(value)
                    if value == last:
                        return

            asyncio.run(asyncio.wait_for(run(), 60))

        threads = [
            threading.Thread(target=watch_sync),
            threading.Thread(target=watch_async),
        ]
        for thread in threads:
            thread.start()
        writer = context.Process(target=count_up, args=(unique_name, last, go))
        writer.start()
        go.set()
        writer.join(60)
        for thread in threads:
            thread.join(60)
        assert not any(thread.is_alive() for thread in threads)
    assert writer.exitcode == 0
    for values in seen.values():
        assert values[-1] == last
        assert values == sorted(set(values))
