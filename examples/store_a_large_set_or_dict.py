"""The script of the guide "How to store a large set or dict"."""

# --8<-- [start:declare]
from typing import Annotated

from sharedbox import BoundedDict, BoundedSet, Capacity, SharedBox


class Catalogue(SharedBox, name="example-catalogue"):
    seen: Annotated[BoundedSet[int], Capacity(65536)]


# --8<-- [end:declare]

# --8<-- [start:create]
catalogue = Catalogue(BoundedSet[int](range(50000), capacity=65536))
# --8<-- [end:create]

# --8<-- [start:change]
seen = catalogue.seen
print(49999 in seen, 50000 in seen)  # True False
seen.add(50000)
seen.discard(0)
catalogue.seen = seen
print(len(catalogue.seen), 0 in catalogue.seen)  # 50000 False
# --8<-- [end:change]

# --8<-- [start:combine]
recent = seen & {49999, 50000, 70000}
print(type(recent).__name__, sorted(recent))  # BoundedSet [49999, 50000]
seen |= {60000, 60001}
print(len(seen))  # 50002
# --8<-- [end:combine]

# --8<-- [start:too-many]
try:
    seen.update(range(100000, 120000))
except ValueError as error:
    print(
        error
    )  # Catalogue.seen holds at most 65536 elements; the value would have 65537
print(len(seen))  # 50002
# --8<-- [end:too-many]

# --8<-- [start:refused]
try:

    class Prices(SharedBox, name="example-prices"):
        levels: Annotated[BoundedSet[int | float], Capacity(16)]

except TypeError as error:
    print(
        error
    )  # Prices.levels: BoundedSet keys cannot hold a union of int:8 and float:8
# --8<-- [end:refused]

# --8<-- [start:convert]
plain = set(catalogue.seen)
print(type(plain).__name__, len(plain))  # set 50000
# --8<-- [end:convert]


# --8<-- [start:subclass]
class Tags(BoundedSet[int]):
    def newest(self) -> int:
        return max(self)


class Labels(SharedBox, name="example-labels"):
    tags: Annotated[Tags, Capacity(1024)]


labels = Labels(Tags([3, 7, 5], capacity=1024))
print(labels.tags.newest())  # 7
# --8<-- [end:subclass]


# --8<-- [start:dict-declare]
class Scores(SharedBox, name="example-scores"):
    by_id: Annotated[BoundedDict[int, float], Capacity(4096)]


scores = Scores(BoundedDict[int, float](dict.fromkeys(range(3000), 1.0), capacity=4096))
by_id = scores.by_id
print(by_id[2999], by_id.get(5000), 5000 in by_id)  # 1.0 None False
# --8<-- [end:dict-declare]

# --8<-- [start:dict-change]
by_id[0] = 0.5
del by_id[1]
by_id[5000] = 2.0
scores.by_id = by_id
keys = list(scores.by_id)
print(keys[:3], keys[-1], scores.by_id[0])  # [0, 2, 3] 5000 0.5
# --8<-- [end:dict-change]

# --8<-- [start:dict-nan]
by_id[7] = float("nan")
print(by_id == by_id.copy())  # False
# --8<-- [end:dict-nan]

scores.close()
Scores.unlink()
labels.close()
Labels.unlink()
catalogue.close()
Catalogue.unlink()
