"""The script of the guide "How to request work from the process that owns a box"."""

# --8<-- [start:declare]
import asyncio
import threading

from sharedbox import (
    Attr,
    RequestError,
    RequestSupersededError,
    Setting,
    SharedBox,
    attr,
)


class Camera(SharedBox, name="example-camera"):
    temperature: Attr[float] = attr()
    exposure: Setting[float] = attr(default=0.01)
    move: Attr[float, [float]] = attr()
    home: Attr[None] = attr()


# --8<-- [end:declare]

# --8<-- [start:publish]
camera = Camera()
camera.temperature.publish(value=21.5)
print(camera.temperature.value)  # 21.5
reading = camera.temperature.read()
print(reading["value"], reading["timestamp_ns"] > 0)  # 21.5 True
# --8<-- [end:publish]


# --8<-- [start:serve]
def set_exposure(value: float) -> float:
    accepted = min(max(value, 0.001), 1.0)
    camera.exposure.publish(setpoint=accepted)
    camera.exposure.publish(value=accepted)
    return accepted


def move(target: float) -> float:
    if target > 100.0:
        raise ValueError(f"{target} is out of range")
    return target


exposure_server = camera.exposure.serve(set_exposure)
move_server = camera.move.serve(move)
# --8<-- [end:serve]

# --8<-- [start:request]
print(camera.exposure.set(5.0).result(timeout=5))  # 1.0
print(camera.exposure.setpoint)  # 1.0
print(camera.move(5.0).result(timeout=5))  # 5.0


async def main() -> None:
    print(await camera.exposure.set(0.02))  # 0.02


asyncio.run(main())
# --8<-- [end:request]

# --8<-- [start:error]
try:
    camera.move(250.0).result(timeout=5)
except RequestError as error:
    print(error)  # Camera.move: ValueError: 250.0 is out of range
# --8<-- [end:error]

move_server.stop()

# --8<-- [start:supersede]
started = threading.Event()
release = threading.Event()


def slow_move(target: float) -> float:
    started.set()
    release.wait()
    return target


move_server = camera.move.serve(slow_move)
first = camera.move(1.0)
started.wait()
second = camera.move(2.0)
third = camera.move(3.0)
release.set()
print(first.result(timeout=5), third.result(timeout=5))  # 1.0 3.0
try:
    second.result(timeout=5)
except RequestSupersededError:
    print("the second request was superseded")
# --8<-- [end:supersede]

move_server.stop()
exposure_server.stop()
camera.close()
Camera.unlink()
