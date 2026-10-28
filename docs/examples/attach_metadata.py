"""The script of the guide "How to attach metadata to a box or stream"."""

# --8<-- [start:box]
from dataclasses import dataclass
from typing import TypedDict

from sharedbox import SharedBox, SharedStream


class Calibration(TypedDict):
    unit: str
    offset: float


class Scale(SharedBox[Calibration]):
    weight: float = 0.0


scale = Scale.create("example-scale", metadata={"unit": "kg", "offset": 0.0})
print(scale.metadata_version)  # 1
print(scale.metadata)  # {'unit': 'kg', 'offset': 0.0}
# --8<-- [end:box]

# --8<-- [start:add]
version = scale.add_metadata({"unit": "kg", "offset": 0.25})
print(version)  # 2
print(scale.metadata_version_of(1)["offset"])  # 0.0
print(scale.metadata_version_of(2)["offset"])  # 0.25
# --8<-- [end:add]


# --8<-- [start:capacity]
class Display(SharedBox[Calibration], metadata_capacity=4096, metadata_versions=8):
    weight: float = 0.0


display = Display.create("example-display")
print(display.metadata)  # None
display.add_metadata({"unit": "g", "offset": 0.0})
# --8<-- [end:capacity]

# --8<-- [start:read-only]
stored = scale.metadata_version_of(2)
try:
    stored["offset"] = 0.5
except TypeError as error:
    print(error)  # 'mappingproxy' object does not support item assignment
scale.add_metadata({**stored, "offset": 0.5})
# --8<-- [end:read-only]


# --8<-- [start:stream]
@dataclass(frozen=True)
class Reading:
    weight: float


with SharedStream.create(
    Reading,
    "example-scale:readings",
    capacity=8,
    metadata=Calibration(unit="kg", offset=0.0),
) as stream:
    reader = stream.reader()
    with stream.sender() as sender:
        sender.send(Reading(1.5))
        sender.add_metadata(Calibration(unit="kg", offset=0.25))
        sender.send(Reading(1.75))
    for reading in reader:
        calibration = reader.metadata
        if calibration is not None:
            print(reader.metadata_version, reading.weight - calibration["offset"])
            # prints 1 1.5, then 2 1.5
SharedStream.unlink("example-scale:readings")
# --8<-- [end:stream]

scale.close()
display.close()
Scale.unlink("example-scale")
Display.unlink("example-display")
