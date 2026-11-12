from typing import Annotated, assert_type

import numpy as np
import numpy.typing as npt

from sharedbox import Shape, SharedBox


class Sensor(SharedBox):
    line: Annotated[npt.NDArray[np.float32], Shape((8,))]
    frame: Annotated[np.ndarray[tuple[int, int], np.dtype[np.uint8]], Shape((4, 6))] = (
        np.zeros((4, 6), np.uint8)
    )
    gain: Annotated[npt.NDArray[np.float64], Shape((2,))] = np.zeros(2)


def fields(sensor: Sensor) -> None:
    assert_type(sensor.frame, np.ndarray[tuple[int, int], np.dtype[np.uint8]])
    assert_type(sensor.line, npt.NDArray[np.float32])
    sensor.frame = np.zeros((4, 6), np.uint8)
    sensor.line = np.zeros(8, np.float32)
    sensor.frame = 3  # type: ignore[assignment]
