"""The script of the guide "How to store arrays"."""

# --8<-- [start:declare]
import dataclasses
from typing import Annotated

import numpy as np
import numpy.typing as npt

from sharedbox import DType, Shape, SharedBox, SupportsDLPack, field


class Scanner(SharedBox, name="example-scanner"):
    scan: npt.NDArray[np.uint8] = np.zeros((4, 6), np.uint8)
    gain: npt.NDArray[np.float64] = field(shape=(2,), default=np.ones(2))
    raw: Annotated[SupportsDLPack, Shape(3), DType("float32")] = np.zeros(3, np.float32)


# --8<-- [end:declare]


# --8<-- [start:record]
@dataclasses.dataclass
class Page:
    number: int
    scan: npt.NDArray[np.uint8] = dataclasses.field(metadata={"shape": (4, 6)})


class Binder(SharedBox, name="example-binder"):
    page: Page


binder = Binder(Page(1, np.zeros((4, 6), np.uint8)))
print(binder.page.scan.shape)  # (4, 6)
# --8<-- [end:record]

# --8<-- [start:write]
scanner = Scanner()
scanner.scan = np.full((4, 6), 7, np.uint8)
print(int(scanner.scan.sum()))  # 168
# --8<-- [end:write]

# --8<-- [start:read-into]
scan = np.empty((4, 6), np.uint8)
scanner.read_into("scan", scan)
print(int(scan.sum()))  # 168
# --8<-- [end:read-into]

# --8<-- [start:writing]
with scanner.writing("scan") as scan:
    scan[0, :] = 255
print(int(scanner.scan[0].sum()))  # 1530
# --8<-- [end:writing]

# --8<-- [start:any-library]
raw = scanner.raw
print(np.from_dlpack(raw))  # [0. 0. 0.]
# --8<-- [end:any-library]


# --8<-- [start:optional]
class Tray(SharedBox, name="example-tray"):
    scan: npt.NDArray[np.uint8] | None = field(default=None, shape=(4, 6))


tray = Tray()
print(tray.scan)  # None
tray.scan = np.ones((4, 6), np.uint8)
print(int(tray.scan.sum()))  # 24
tray.scan = None
print(tray.scan)  # None
# --8<-- [end:optional]

# --8<-- [start:wrong-shape]
try:
    scanner.scan = np.zeros((6, 4), np.uint8)
except ValueError as error:
    # Scanner.scan holds an array of the field's Shape; the value's shape differs
    print(error)
# --8<-- [end:wrong-shape]

scanner.close()
Scanner.unlink()
tray.close()
Tray.unlink()
binder.close()
Binder.unlink()
