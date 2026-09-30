"""The script of the guide "How to send a box to another process"."""

# --8<-- [start:argument]
import multiprocessing as mp

from sharedbox import SharedBox


class Status(SharedBox, name="example-status"):
    last_item: int = -1


def report(status: Status) -> None:
    status.last_item = 99
    status.close()


# --8<-- [end:argument]


# --8<-- [start:pool]
worker_status: Status


def start_worker(name: str) -> None:
    global worker_status
    worker_status = Status.attach(name)


def work(item: int) -> int:
    worker_status.last_item = item
    return item * 2


# --8<-- [end:pool]


# --8<-- [start:main]
if __name__ == "__main__":
    context = mp.get_context("spawn")
    with Status() as status:
        child = context.Process(target=report, args=(status,))
        child.start()
        child.join()
        print(status.last_item)  # 99
        with context.Pool(4, initializer=start_worker, initargs=(status.name,)) as pool:
            print(sum(pool.map(work, range(100))))  # 9900
    Status.unlink()
# --8<-- [end:main]
