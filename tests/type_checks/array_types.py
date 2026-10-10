from typing import assert_type

import numpy as np
import numpy.typing as npt

from sharedbox import SharedBox, field


class Sensor(SharedBox):
    line: npt.NDArray[np.float32] = field(shape=(8,))
    frame: np.ndarray[tuple[int, int], np.dtype[np.uint8]] = np.zeros((4, 6), np.uint8)
    gain: npt.NDArray[np.float64] = field(shape=(2,), default=np.zeros(2))


def fields(sensor: Sensor) -> None:
    assert_type(sensor.frame, np.ndarray[tuple[int, int], np.dtype[np.uint8]])
    assert_type(sensor.line, npt.NDArray[np.float32])
    sensor.frame = np.zeros((4, 6), np.uint8)
    sensor.line = np.zeros(8, np.float32)
    sensor.frame = 3  # type: ignore[assignment]
