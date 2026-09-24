import multiprocessing as mp
import statistics
import time

from sharedbox import SharedBox

BATCH = 100
ROUNDS = 200
PINGS = 2_000


class Sample(SharedBox):
    ping: int = 0
    pong: int = 0
    level: float = 0.0


def median_us(fn, rounds: int = ROUNDS, batch: int = BATCH) -> float:
    samples = []
    for _ in range(rounds):
        start = time.perf_counter_ns()
        for _ in range(batch):
            fn()
        samples.append((time.perf_counter_ns() - start) / batch)
    return statistics.median(samples) / 1000


def responder(name: str) -> None:
    box = Sample.attach(name)
    pings = box.watch("ping")
    box.pong = -1
    for seen, value in enumerate(pings, start=1):
        box.pong = value
        if seen == PINGS:
            break
    box.close()


def round_trip_us(box: Sample) -> float:
    pongs = iter(box.watch("pong"))
    child = mp.get_context("spawn").Process(target=responder, args=(box.name,))
    child.start()
    next(pongs)
    samples = []
    for i in range(1, PINGS + 1):
        start = time.perf_counter_ns()
        box.ping = i
        next(pongs)
        samples.append(time.perf_counter_ns() - start)
    child.join()
    return statistics.median(samples) / 1000


def main() -> None:
    value = mp.Value("d", 0.0)
    with Sample() as box:
        rows = [
            ("SharedBox write", median_us(lambda: setattr(box, "level", 1.0))),
            ("SharedBox read", median_us(lambda: box.level)),
            (
                "SharedBox update(2 fields)",
                median_us(lambda: box.update(ping=1, level=1.0)),
            ),
            (
                "mp.Value write (with lock)",
                median_us(lambda: setattr(value, "value", 1.0)),
            ),
            ("mp.Value read (with lock)", median_us(lambda: value.value)),
            ("watch() round trip", round_trip_us(box)),
        ]
    Sample.unlink()
    for label, us in rows:
        print(f"{label:32} {us:8.2f} us")


if __name__ == "__main__":
    main()
