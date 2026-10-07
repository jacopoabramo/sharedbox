"""The script of the guide "How to store arrays"."""

# --8<-- [start:declare]
from typing import Annotated

import numpy as np

from sharedbox import DType, Shape, SharedBox, SupportsDLPack


class Sensor(SharedBox, name="example-sensor"):
    frame: Annotated[np.ndarray, Shape(4, 6), DType("uint8")] = np.zeros(
        (4, 6), np.uint8
    )
    raw: Annotated[SupportsDLPack, Shape(3), DType("float32")] = np.zeros(3, np.float32)


# --8<-- [end:declare]

# --8<-- [start:write]
sensor = Sensor()
sensor.frame = np.full((4, 6), 7, np.uint8)
print(int(sensor.frame.sum()))  # 168
# --8<-- [end:write]

# --8<-- [start:read-into]
frame = np.empty((4, 6), np.uint8)
sensor.read_into("frame", frame)
print(int(frame.sum()))  # 168
# --8<-- [end:read-into]

# --8<-- [start:any-library]
raw = sensor.raw
print(np.from_dlpack(raw))  # [0. 0. 0.]
# --8<-- [end:any-library]

# --8<-- [start:wrong-shape]
try:
    sensor.frame = np.zeros((6, 4), np.uint8)
except ValueError as error:
    # Sensor.frame holds an array of the field's Shape; the value's shape differs
    print(error)
# --8<-- [end:wrong-shape]

sensor.close()
Sensor.unlink()
