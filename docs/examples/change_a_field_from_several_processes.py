"""The script of the guide "How to change a field from several processes"."""

# --8<-- [start:lost]
import multiprocessing as mp
from collections.abc import Callable
from typing import Annotated

from sharedbox import Capacity, SharedBox


class Job(SharedBox, name="example-job"):
    count: int = 0
    state: Annotated[str, Capacity(16)] = "idle"
    owner: Annotated[str, Capacity(16)] = ""


def count_unlocked(job: Job) -> None:
    for _ in range(10_000):
        job.count += 1
    job.close()


# --8<-- [end:lost]


# --8<-- [start:locked]
def count_locked(job: Job) -> None:
    for _ in range(10_000):
        with job.locked() as j:
            j.count += 1
    job.close()


# --8<-- [end:locked]


# --8<-- [start:claim]
def claim(job: Job, me: str) -> bool:
    with job.locked() as j:
        if j.state != "idle":
            return False
        j.state = "busy"
        j.owner = me
        return True


# --8<-- [end:claim]


def run_twice(job: Job, target: Callable[[Job], object]) -> None:
    context = mp.get_context("spawn")
    workers = [context.Process(target=target, args=(job,)) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()


if __name__ == "__main__":
    with Job() as job:
        # --8<-- [start:run]
        run_twice(job, count_unlocked)
        print(job.count <= 20_000)  # True, and often below 20000
        job.count = 0
        run_twice(job, count_locked)
        print(job.count)  # 20000
        # --8<-- [end:run]

        # --8<-- [start:claimed]
        print(claim(job, "first"), claim(job, "second"))  # True False
        print(job.owner)  # first
        # --8<-- [end:claimed]

        # --8<-- [start:rollback]
        try:
            with job.locked() as j:
                j.count = 0
                raise RuntimeError("changed my mind")
        except RuntimeError:
            pass
        print(job.count)  # 20000
        # --8<-- [end:rollback]
    Job.unlink()
