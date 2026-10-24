"""The script of the guide "How to send a box to another process"."""

# --8<-- [start:argument]
import multiprocessing as mp

from sharedbox import SharedBox


class Sorter(SharedBox, name="example-sorter"):
    last_letter: int = -1


def report(sorter: Sorter) -> None:
    sorter.last_letter = 99
    sorter.close()


# --8<-- [end:argument]


# --8<-- [start:pool]
worker_sorter: Sorter


def start_worker(name: str) -> None:
    global worker_sorter
    worker_sorter = Sorter.attach(name)


def work(item: int) -> int:
    worker_sorter.last_letter = item
    return item * 2


# --8<-- [end:pool]


# --8<-- [start:main]
if __name__ == "__main__":
    context = mp.get_context("spawn")
    with Sorter() as sorter:
        child = context.Process(target=report, args=(sorter,))
        child.start()
        child.join()
        print(sorter.last_letter)  # 99
        with context.Pool(4, initializer=start_worker, initargs=(sorter.name,)) as pool:
            print(sum(pool.map(work, range(100))))  # 9900
    Sorter.unlink()
# --8<-- [end:main]
