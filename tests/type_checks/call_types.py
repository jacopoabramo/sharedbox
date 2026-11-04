from typing import assert_type

from sharedbox import Call, RequestFuture, RequestServer, SharedBox, call, field


class Scanner(SharedBox):
    position: float = 0.0
    move: Call[[float], float] = call()
    home: Call[[], None] = field(init=False)


def sync_handler(position: float) -> float:
    return position


async def async_handler(position: float) -> float:
    return position


def wrong_handler(x: str) -> float:
    return 0.0


def calls(box: Scanner) -> None:
    assert_type(box.move(1.0), RequestFuture[float])
    assert_type(box.home(), RequestFuture[None])
    assert_type(box.move.serve(sync_handler), RequestServer)
    assert_type(box.move.serve(async_handler), RequestServer)
    box.move("x")  # type: ignore[arg-type]
    box.move.serve(wrong_handler)  # type: ignore[arg-type]
    Scanner()


async def awaited(box: Scanner) -> None:
    assert_type(await box.move(1.0), float)
