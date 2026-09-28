from typing import Annotated, assert_type

import sharedbox
from sharedbox import Capacity, Field, SharedBox, SupportsSharedBox, field


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


class Frame(SharedBox, identity="camera/frame/1", max_waiters=8):
    exposure: float


def run(frame: SupportsSharedBox) -> None: ...


def protocol(motor: Motor, frame: Frame) -> None:
    run(motor)
    run(frame)
    run(object())  # type: ignore[arg-type]


class Stage(SharedBox):
    x: float
    label: Annotated[str, Capacity(8)] = field(default="s")
    moves: int = field(default=0, init=False)
    speed: float = field(default_factory=float)


def field_options(stage: Stage) -> None:
    assert_type(Stage(1.0), Stage)
    Stage(1.0, "t", 2.0)
    Stage(1.0, moves=3)  # type: ignore[call-arg]
    assert_type(stage.moves, int)
    assert_type(sharedbox.fields(stage), tuple[Field, ...])
