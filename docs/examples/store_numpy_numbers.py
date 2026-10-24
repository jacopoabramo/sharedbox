"""The script of the guide "How to store numpy numbers"."""

# --8<-- [start:input]
import numpy as np

from sharedbox import SharedBox


class Counter(SharedBox, name="example-counter"):
    parcels: int = 0
    weight: float = 0.0


counter = Counter()
scans = np.array([3, 7, 5], np.uint16)
weights = np.array([0.5, 1.5], np.float32)
counter.parcels = scans.max()
print(repr(counter.parcels))  # 7
counter.weight = weights[1]
print(repr(counter.weight))  # 1.5
# --8<-- [end:input]


# --8<-- [start:declare]
class Scale(SharedBox, name="example-scale"):
    grams: np.uint16 = np.uint16(0)
    reading: np.float32 = np.float32(0.0)
    raw: np.int64 = np.int64(0)


scale = Scale()
scale.grams = np.uint16(1200)
print(repr(scale.grams))  # np.uint16(1200)
scale.reading = np.float32(0.1)
print(repr(scale.reading))  # np.float32(0.1)
print(repr(scale.raw))  # np.int64(0)
# --8<-- [end:declare]

# --8<-- [start:overflow]
totals = np.array([70000], np.uint32)
try:
    scale.grams = totals[0]
except OverflowError as error:
    # Scale.grams holds an unsigned 16-bit integer; the value does not fit
    print(error)
samples = np.array([1e300])
try:
    scale.reading = samples[0]
except OverflowError as error:
    # Scale.reading holds a 32-bit float; the value does not fit
    print(error)
print(repr(scale.grams))  # np.uint16(1200)
# --8<-- [end:overflow]


# --8<-- [start:time]
class Delivery(SharedBox, name="example-delivery"):
    left_at: np.datetime64 = np.datetime64("NaT")


delivery = Delivery()
delivery.left_at = np.datetime64("2026-10-24T09:30", "m")
print(repr(delivery.left_at))  # np.datetime64('2026-10-24T09:30:00.000000000')
# --8<-- [end:time]


# --8<-- [start:union]
class Envelope(SharedBox, name="example-envelope"):
    stamp: float | int = 0


envelope = Envelope()
counts = np.array([5], np.int64)
envelope.stamp = counts[0]
print(repr(envelope.stamp))  # 5
envelope.stamp = weights[0]
print(repr(envelope.stamp))  # 0.5
# --8<-- [end:union]

for box in (counter, scale, delivery, envelope):
    box.close()
    type(box).unlink()
