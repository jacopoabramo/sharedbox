from typing import Any, Literal, assert_type

import numpy as np

from sharedbox import SharedBox

Image = np.ndarray[tuple[Literal[512], Literal[512]], np.dtype[np.uint16]]


class Camera(SharedBox):
    frame: Image
    line: np.ndarray[tuple[Literal[8]], np.dtype[np.float32]]


def fields(camera: Camera) -> None:
    assert_type(camera.frame, Image)
    assert_type(camera.line, np.ndarray[tuple[Literal[8]], np.dtype[np.float32]])
    frame: Any = np.zeros((512, 512), np.uint16)
    camera.frame = frame
    camera.frame = 3  # type: ignore[assignment]
