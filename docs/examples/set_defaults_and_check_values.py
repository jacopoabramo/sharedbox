"""The script of the guide "How to set defaults and check values"."""

# --8<-- [start:defaults]
import time
from dataclasses import KW_ONLY, InitVar

from sharedbox import SharedBox, field, fields


class Pump(SharedBox, name="example-pump"):
    flow: float = 0.0
    started: float = field(default_factory=time.time)


with Pump() as pump:
    print(pump.flow, pump.started > 0)  # 0.0 True
Pump.unlink()
# --8<-- [end:defaults]


# --8<-- [start:keyword-only]
class Valve(SharedBox, name="example-valve"):
    opening: float
    _: KW_ONLY
    limit: float = 1.0
    closing_time: float = 0.5


with Valve(0.2, closing_time=2.0) as valve:
    print(valve)  # Valve(opening=0.2, limit=1.0, closing_time=2.0)
Valve.unlink()
# --8<-- [end:keyword-only]


# --8<-- [start:check]
class Heater(SharedBox, name="example-heater"):
    target: float
    limit: float = 80.0

    def __post_init__(self) -> None:
        if self.target > self.limit:
            raise ValueError(f"target {self.target} is above the limit {self.limit}")


try:
    Heater(95.0)
except ValueError as error:
    print(error)  # target 95.0 is above the limit 80.0
with Heater(60.0) as heater:
    print(heater.target)  # 60.0
Heater.unlink()
# --8<-- [end:check]


# --8<-- [start:init-var]
class Scale(SharedBox, name="example-scale"):
    grams: float = 0.0
    kilograms: InitVar[float] = 0.0

    def __post_init__(self, kilograms: float) -> None:
        self.grams += kilograms * 1000


with Scale(kilograms=1.5) as scale:
    print(scale.snapshot())  # {'grams': 1500.0}
Scale.unlink()
# --8<-- [end:init-var]


# --8<-- [start:describe]
class Stage(SharedBox, name="example-stage"):
    position: float = field(
        default=0.0, metadata={"unit": "mm"}, doc="Distance from home."
    )


print(fields(Stage)[0].metadata["unit"])  # mm
print(fields(Stage)[0].doc)  # Distance from home.
# --8<-- [end:describe]
