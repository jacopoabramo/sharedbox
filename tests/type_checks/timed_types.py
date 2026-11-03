from typing import assert_type

from sharedbox import SharedBox, Timed


class Thermometer(SharedBox):
    temperature: Timed[float]


def timed_values(box: Thermometer) -> None:
    assert_type(Timed.now(1.5), Timed[float])
    assert_type(box.temperature, Timed[float])
    assert_type(box.temperature.timestamp, float)
    box.temperature = Timed.now(21.5)
    box.temperature = 21.5  # type: ignore[assignment]
