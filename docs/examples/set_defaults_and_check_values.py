"""The script of the guide "How to set defaults and check values"."""

# --8<-- [start:defaults]
import time
from dataclasses import KW_ONLY, InitVar

from sharedbox import SharedBox, field, fields


class Counter(SharedBox, name="example-counter"):
    till: float = 0.0
    opened: float = field(default_factory=time.time)


with Counter() as counter:
    print(counter.till, counter.opened > 0)  # 0.0 True
Counter.unlink()
# --8<-- [end:defaults]


# --8<-- [start:keyword-only]
class Postage(SharedBox, name="example-postage"):
    weight: float
    _: KW_ONLY
    rate: float = 1.0
    surcharge: float = 0.5


with Postage(0.2, surcharge=2.0) as postage:
    print(postage)  # Postage(weight=0.2, rate=1.0, surcharge=2.0)
Postage.unlink()
# --8<-- [end:keyword-only]


# --8<-- [start:check]
class Parcel(SharedBox, name="example-parcel"):
    weight: float
    limit: float = 30.0

    def __post_init__(self) -> None:
        if self.weight > self.limit:
            raise ValueError(f"weight {self.weight} is above the limit {self.limit}")


try:
    Parcel(35.0)
except ValueError as error:
    print(error)  # weight 35.0 is above the limit 30.0
with Parcel(12.0) as parcel:
    print(parcel.weight)  # 12.0
Parcel.unlink()
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
class Route(SharedBox, name="example-route"):
    distance: float = field(
        default=0.0, metadata={"unit": "km"}, doc="Distance from the post office."
    )


print(fields(Route)[0].metadata["unit"])  # km
print(fields(Route)[0].doc)  # Distance from the post office.
# --8<-- [end:describe]
