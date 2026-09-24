from typing import Annotated, assert_type

from sharedbox import Capacity, SharedBox


class Motor(SharedBox):
    position: int
    enabled: bool
    label: Annotated[str, Capacity(32)]


class Config(SharedBox, kw_only=True):
    rate: float
    retries: int = 0


def constructors() -> None:
    motor = Motor(1, False, "x")
    assert_type(motor, Motor)
    assert_type(Motor.attach(), Motor)
    assert_type(Motor.create("motor-2", 1, False, "x"), Motor)
    assert_type(Config(rate=1.0), Config)
    Motor(1, False)  # type: ignore[call-arg]
    Motor("1", False, "x")  # type: ignore[arg-type]
    Config(1.0)  # type: ignore[call-arg]


def fields(motor: Motor) -> None:
    assert_type(motor.position, int)
    assert_type(motor.label, str)
    motor.position = "3"  # type: ignore[assignment]
    motor.postion = 3  # type: ignore[attr-defined]
