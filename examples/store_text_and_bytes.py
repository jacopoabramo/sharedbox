"""The script of the guide "How to store text and bytes"."""

# --8<-- [start:declare]
from typing import Annotated

from sharedbox import Capacity, SharedBox


class Camera(SharedBox, name="example-camera"):
    label: Annotated[str, Capacity(16)] = ""
    frame: Annotated[bytes, Capacity(1024)] = b""


# --8<-- [end:declare]

# --8<-- [start:write]
camera = Camera()
camera.label = "front"
camera.frame = bytes(range(8))
print(camera.label, len(camera.frame))  # front 8
# --8<-- [end:write]

# --8<-- [start:too-long]
try:
    camera.label = "front camera, left side"
except ValueError as error:
    print(error)  # Camera.label holds at most 16 bytes; the value encodes to 23
print(camera.label)  # front
# --8<-- [end:too-long]

# --8<-- [start:bytes-not-characters]
print(len("été"), len("été".encode()))  # 3 5
# --8<-- [end:bytes-not-characters]


# --8<-- [start:fit]
def fit(text: str, size: int) -> str:
    return text.encode()[:size].decode(errors="ignore")


camera.label = fit("front camera, left side", 16)
print(camera.label)  # front camera, le
# --8<-- [end:fit]

camera.close()
Camera.unlink()
