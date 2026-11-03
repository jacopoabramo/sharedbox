"""The script of the guide "How to call a function in the process that owns a box"."""

# --8<-- [start:declare]
import asyncio
import threading

from sharedbox import Call, RequestError, RequestSupersededError, SharedBox, call


class Scanner(SharedBox, name="example-scanner"):
    position: float = 0.0
    move: Call[[float], float] = call()
    home: Call[[], None] = call()


# --8<-- [end:declare]

# --8<-- [start:serve]
scanner = Scanner()


def move(target: float) -> float:
    if target > 100.0:
        raise ValueError(f"{target} is out of range")
    scanner.position = target
    return target


server = scanner.move.serve(move)
# --8<-- [end:serve]

# --8<-- [start:call]
print(scanner.move(5.0).result(timeout=5))  # 5.0


async def main() -> None:
    print(await scanner.move(7.5))  # 7.5


asyncio.run(main())
# --8<-- [end:call]

# --8<-- [start:error]
try:
    scanner.move(250.0).result(timeout=5)
except RequestError as error:
    print(error)  # Scanner.move: ValueError: 250.0 is out of range
# --8<-- [end:error]

server.stop()

# --8<-- [start:supersede]
started = threading.Event()
release = threading.Event()


def slow_move(target: float) -> float:
    started.set()
    release.wait()
    return target


server = scanner.move.serve(slow_move)
first = scanner.move(1.0)
started.wait()
second = scanner.move(2.0)
third = scanner.move(3.0)
release.set()
print(first.result(timeout=5), third.result(timeout=5))  # 1.0 3.0
try:
    second.result(timeout=5)
except RequestSupersededError:
    print("the second request was superseded")
# --8<-- [end:supersede]

server.stop()
scanner.close()
Scanner.unlink()
