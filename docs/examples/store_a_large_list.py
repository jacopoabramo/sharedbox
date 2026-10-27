"""The script of the guide "How to store a large list"."""

# --8<-- [start:declare]
from typing import Annotated

import numpy as np

from sharedbox import BoundedList, Capacity, SharedBox


class Recording(SharedBox, name="example-recording"):
    samples: Annotated[BoundedList[float], Capacity(65536)]


# --8<-- [end:declare]

# --8<-- [start:create]
recording = Recording(BoundedList[float]([0.0] * 50000, capacity=65536))
# --8<-- [end:create]

# --8<-- [start:change]
samples = recording.samples
samples[0] = 1.5
samples.append(2.5)
recording.samples = samples
print(len(recording.samples), recording.samples[0])  # 50001 1.5
# --8<-- [end:change]

# --8<-- [start:too-many]
try:
    samples.extend([0.0] * 20000)
except ValueError as error:
    print(
        error
    )  # Recording.samples holds at most 65536 elements; the value would have 70001
print(len(samples))  # 50001
# --8<-- [end:too-many]

# --8<-- [start:convert]
plain = list(recording.samples)
print(type(plain).__name__, len(plain))  # list 50001
# --8<-- [end:convert]

# --8<-- [start:numpy]
samples = recording.samples
array = np.asarray(samples)
print(array.dtype, array.shape)  # float64 (50001,)
array[:3] = [4.0, 5.0, 6.0]
del array  # releases the view, so the length can change again
recording.samples = samples
print(list(recording.samples[:3]))  # [4.0, 5.0, 6.0]
# --8<-- [end:numpy]

# --8<-- [start:last-n]
batch = [7.0] * 20000
samples = recording.samples
del samples[: max(0, len(samples) + len(batch) - samples.capacity)]
samples.extend(batch)
recording.samples = samples
print(len(recording.samples), recording.samples[-1])  # 65536 7.0
# --8<-- [end:last-n]


# --8<-- [start:subclass]
class Samples(BoundedList[float]):
    def peak(self) -> float:
        return max(self)


class Analysis(SharedBox, name="example-analysis"):
    samples: Annotated[Samples, Capacity(1024)]


analysis = Analysis(Samples([0.5, 3.0, 1.0], capacity=1024))
print(analysis.samples.peak())  # 3.0
# --8<-- [end:subclass]

analysis.close()
Analysis.unlink()
recording.close()
Recording.unlink()
