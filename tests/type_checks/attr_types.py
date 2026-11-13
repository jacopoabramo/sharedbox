from typing import Annotated, assert_type

from sharedbox import (
    Attr,
    AttrValue,
    Capacity,
    RequestFuture,
    RequestServer,
    Setting,
    SharedBox,
    attr,
)


class Camera(SharedBox):
    temperature: Attr[float] = attr()
    exposure: Setting[float] = attr()
    move: Attr[float, [float, Annotated[str, Capacity(16)]]] = attr()
    home: Attr[None] = attr()


def sync_handler(position: float, speed: str) -> float:
    return position


async def async_handler(position: float, speed: str) -> float:
    return position


async def set_exposure(value: float) -> float:
    return value


def attributes(camera: Camera) -> None:
    assert_type(camera.exposure.value, float)
    assert_type(camera.exposure.set(0.02), RequestFuture[float])
    assert_type(camera.move(1.0, "fast"), RequestFuture[float])
    assert_type(camera.home(), RequestFuture[None])
    assert_type(camera.exposure.read(), AttrValue[float])
    assert_type(camera.move.serve(sync_handler), RequestServer)
    assert_type(camera.move.serve(async_handler), RequestServer)
    assert_type(camera.exposure.serve(set_exposure), RequestServer)
    camera.move.serve(set_exposure)  # type: ignore[arg-type]
    camera.move("x", "fast")  # type: ignore[arg-type]
    camera.exposure.set("x")  # type: ignore[arg-type]
    assert_type(camera.exposure.setpoint, float)
    assert_type(camera.temperature.timestamp_ns, int)
    assert_type(camera.temperature.read()["timestamp_ns"], int)
    camera.temperature.set(1.0)  # type: ignore[attr-defined]
    camera.temperature.publish(setpoint=1.0)  # type: ignore[call-arg]
    camera.temperature.serve(set_exposure)  # type: ignore[arg-type]
    Camera()


async def awaited(camera: Camera) -> None:
    assert_type(await camera.move(1.0, "fast"), float)
