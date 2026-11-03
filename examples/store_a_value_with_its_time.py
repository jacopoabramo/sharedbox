"""The script of the guide "How to store a value with its time"."""
# ruff: noqa: RUF012

# --8<-- [start:declare]
from typing import Annotated

from sharedbox import Capacity, SharedBox, SharedStream, Timed


class Thermometer(SharedBox, name="example-thermometer"):
    temperature: Timed[float] = Timed(0.0, 0)
    status: Timed[Annotated[str, Capacity(16)]] = Timed("", 0)
    history: Annotated[list[Timed[float]], Capacity(4)] = []


# --8<-- [end:declare]

# --8<-- [start:stamp]
thermometer = Thermometer()
thermometer.temperature = Timed.now(21.5)
reading = thermometer.temperature
print(reading.value)  # 21.5
print(reading.timestamp_ns > 0)  # True
print(type(reading.timestamp))  # <class 'float'>
# --8<-- [end:stamp]

# --8<-- [start:given]
device_ns = 1_762_000_000_123_456_789
thermometer.status = Timed("ready", device_ns)
print(thermometer.status)  # Timed(value='ready', timestamp_ns=1762000000123456789)
# --8<-- [end:given]

# --8<-- [start:collections]
thermometer.history = [Timed.now(21.4), Timed.now(21.5)]
first, second = thermometer.history
print(first.timestamp_ns == second.timestamp_ns)  # True
# --8<-- [end:collections]

# --8<-- [start:stream]
with SharedStream.create(Timed[float], "example-temperatures", capacity=4) as stream:
    reader = stream.reader()
    with stream.sender() as sender:
        sender.send(Timed.now(21.6))
    print(reader.receive().value)  # 21.6
# --8<-- [end:stream]

thermometer.close()
Thermometer.unlink()
SharedStream.unlink("example-temperatures")
