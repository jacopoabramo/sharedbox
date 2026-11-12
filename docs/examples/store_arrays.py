"""The script of the guide "How to store arrays"."""

# --8<-- [start:declare]
import dataclasses
from typing import Annotated

import numpy as np
import numpy.typing as npt

from sharedbox import DType, MaxShape, Shape, SharedBox, SupportsDLPack


class Scanner(SharedBox, name="example-scanner"):
    scan: Annotated[npt.NDArray[np.uint8], Shape((4, 6))] = np.zeros((4, 6), np.uint8)
    gain: Annotated[npt.NDArray[np.float64], Shape((2,))] = np.ones(2)
    raw: Annotated[SupportsDLPack, Shape((3,)), DType("float32")] = np.zeros(
        3, np.float32
    )


# --8<-- [end:declare]


# --8<-- [start:record]
@dataclasses.dataclass
class Page:
    number: int
    scan: Annotated[npt.NDArray[np.uint8], Shape((4, 6))]


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


# --8<-- [start:max-shape]
class Sorter(SharedBox, name="example-sorter"):
    label: Annotated[npt.NDArray[np.uint8], MaxShape((8, 12))] = np.zeros(
        (0, 12), np.uint8
    )


sorter = Sorter()
print(sorter.label.shape)  # (0, 12)
sorter.label = np.ones((3, 12), np.uint8)
print(sorter.label.shape)  # (3, 12)
with sorter.writing("label", shape=(5, 12)) as label:
    label[:] = 2
print(sorter.label.shape, int(sorter.label.sum()))  # (5, 12) 120
# --8<-- [end:max-shape]

# --8<-- [start:any-library]
raw = scanner.raw
print(np.from_dlpack(raw))  # [0. 0. 0.]
# --8<-- [end:any-library]


# --8<-- [start:optional]
class Tray(SharedBox, name="example-tray"):
    scan: Annotated[npt.NDArray[np.uint8], Shape((4, 6))] | None = None


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
sorter.close()
Sorter.unlink()
binder.close()
Binder.unlink()
