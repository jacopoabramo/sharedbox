from typing import assert_type

import numpy as np

from sharedbox import SharedBox


class Sensor(SharedBox):
    frame: np.ndarray[tuple[int, int], np.dtype[np.uint8]] = np.zeros((4, 6), np.uint8)


def fields(sensor: Sensor) -> None:
    assert_type(sensor.frame, np.ndarray[tuple[int, int], np.dtype[np.uint8]])
    sensor.frame = np.zeros((4, 6), np.uint8)
    sensor.frame = 3  # type: ignore[assignment]
