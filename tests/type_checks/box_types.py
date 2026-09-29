from dataclasses import InitVar
from typing import Annotated, Any, assert_type, cast

import sharedbox
from sharedbox import BoxRef, Capacity, Field, SharedBox, SupportsSharedBox, field


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
    offset: InitVar[float] = 0.0

    def __post_init__(self, offset: float) -> None: ...


def field_options(stage: Stage) -> None:
    assert_type(Stage(1.0), Stage)
    Stage(1.0, "t", 2.0)
    Stage(1.0, moves=3)  # type: ignore[call-arg]
    Stage(1.0, "t", 2.0, offset=3.0)
    Stage(1.0, offset="far")  # type: ignore[arg-type]
    assert_type(stage.moves, int)
    assert_type(sharedbox.fields(stage), tuple[Field, ...])


class Wheel(SharedBox, identity="wheel/1"):
    turns: int = 0


class FastWheel(Wheel):
    pass


class WheelCopy(SharedBox, identity="wheel/1"):
    turns: int = 0


class Cart(SharedBox):
    wheel: Wheel | None = None


def references(cart: Cart, fast: FastWheel, copy: WheelCopy) -> None:
    assert_type(cart.wheel, Wheel | None)
    assert_type(cart.snapshot(follow=True), dict[str, Any])
    ref = BoxRef("w", 1, 2)
    assert_type(ref.name, str)
    assert_type(ref.box_class, type[SharedBox] | None)
    Cart(fast)
    cart.wheel = fast
    Cart(copy)  # type: ignore[arg-type]
    cart.wheel = copy  # type: ignore[assignment]
    cart.wheel = cast(Wheel, copy)
