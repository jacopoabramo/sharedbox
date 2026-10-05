"""The script of the guide "How to store records"."""

# --8<-- [start:declare]
from dataclasses import dataclass
from typing import Annotated, NamedTuple, NotRequired, TypedDict

from sharedbox import Capacity, SharedBox, field


@dataclass(frozen=True)
class Position:
    x: float
    y: float


class Limits(NamedTuple):
    low: int
    high: int


class Settings(TypedDict):
    speed: float
    note: NotRequired[Annotated[str, Capacity(16)]]


class Stage(SharedBox, name="example-stage"):
    position: Position = Position(0.0, 0.0)
    limits: Limits = Limits(0, 100)
    settings: Settings = field(default_factory=lambda: {"speed": 1.0})


# --8<-- [end:declare]

# --8<-- [start:write]
stage = Stage()
stage.position = Position(1.5, -2.0)
print(stage.position)  # Position(x=1.5, y=-2.0)
# --8<-- [end:write]

# --8<-- [start:change-one-member]
stage.limits = stage.limits._replace(high=50)
print(stage.limits)  # Limits(low=0, high=50)
# --8<-- [end:change-one-member]

# --8<-- [start:optional-key]
print(stage.settings)  # {'speed': 1.0}
stage.settings = {"speed": 2.0, "note": "slow down"}
print(stage.settings["note"])  # slow down
# --8<-- [end:optional-key]

stage.close()
Stage.unlink()
