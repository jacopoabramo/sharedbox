---
icon: lucide/arrow-up-circle
---

# How to migrate from 0.9

Code written for `sharedbox` 0.9 may need five changes before it runs on
the next release. The size of a field now goes in its annotation only, a
stream call takes its `timeout` by keyword, a sender's `writing` and
`metadata_version_of` have new names, and
[metadata](../explanation/glossary.md#metadata) reads back as a
`frozendict`. Each section below shows the old form and the new one.

## Give sizes in the annotation

A capacity or a shape used to have several spellings: `field(capacity=...)`
and `field(shape=...)` on a box field, the `"shape"` entry of a record
member's field metadata, and the shape of a default array. Each of them is
gone, and the annotation is the one place a size is given, with
[`Capacity`][sharedbox.Capacity] and [`Shape`][sharedbox.Shape] in
`Annotated`. That form already worked in 0.9 and gives the same layout, so
a class moved to it still attaches to a box created by the old one:

```python
# 0.9
class Scanner(SharedBox):
    owner: str = field(capacity=32, default="")
    gain: npt.NDArray[np.float64] = field(shape=(2,))
    scan: npt.NDArray[np.uint8] = np.zeros((4, 6), np.uint8)


@dataclass
class Page:
    scan: npt.NDArray[np.uint8] = dataclasses.field(metadata={"shape": (4, 6)})


# 0.10
class Scanner(SharedBox):
    owner: Annotated[str, Capacity(32)] = ""
    gain: Annotated[npt.NDArray[np.float64], Shape((2,))]
    scan: Annotated[npt.NDArray[np.uint8], Shape((4, 6))] = np.zeros((4, 6), np.uint8)


@dataclass
class Page:
    scan: Annotated[npt.NDArray[np.uint8], Shape((4, 6))]
```

`field()` keeps the options `dataclasses.field` has, so `field(capacity=...)`
and `field(shape=...)` raise `TypeError` for the unknown keyword. An array
field without a `Shape` raises `TypeError` when its class is defined, also
when it has a default array, and so does a record member with `"shape"` in
its metadata. A default array still gives the field its first value, and it
must have the shape the `Shape` names.

## Pass `timeout` by keyword

`timeout` used to be the second positional parameter of some stream calls
and keyword-only in others. It is keyword-only in all of them now:
`send`, `asend`, `reserve` and `areserve` of a sender, `receive` and
`fetch` of a reader, and `receive` of a `sharedbox.bluebox`
`DocumentReceiver`. A call that passed it by position raises `TypeError`:

```python
# 0.9
sender.send(letter, 5)
letter = reader.receive(5)

# 0.10
sender.send(letter, timeout=5)
letter = reader.receive(timeout=5)
```

## Call `reserve` in place of a sender's `writing`

The block that reserves a slot of a stream, fills its arrays in place and
sends it is now [`reserve`][sharedbox.SharedSendStream.reserve], and its
asyncio form is `areserve`. Both take the arguments that `writing` and
`awriting` took and yield the same [`SendSlot`][sharedbox.SendSlot]:

```python
# 0.9
with sender.writing(blank) as slot:
    slot.send(fill(slot.item))

# 0.10
with sender.reserve(blank) as slot:
    slot.send(fill(slot.item))
```

[`SharedBox.writing`][sharedbox.SharedBox.writing], which fills an array
field of a box in place, keeps its name.

## Call `metadata_at` in place of `metadata_version_of`

The method that returns the metadata stored under a version number is now
`metadata_at`, on a box and on a stream. It takes the same argument and
returns the same value:

```python
# 0.9
calibration = box.metadata_version_of(2)

# 0.10
calibration = box.metadata_at(2)
```

Because `metadata_at` is a member of every box, a field or an `InitVar`
can no longer have that name, while `metadata_version_of` is free again.

## Check metadata against `Mapping`, not `MappingProxyType`

A map in a box's or a stream's metadata used to read back as a
`types.MappingProxyType`, which cannot be pickled, deep-copied or
converted to JSON. It now reads back as a `frozendict`: the builtin type
from Python 3.15 on, and the type of the `frozendict` package, which
`sharedbox` installs, on earlier versions. Code that tested for the old
type tests for `collections.abc.Mapping`, which holds on every version:

```python
# 0.9
if isinstance(box.metadata, types.MappingProxyType):
    ...

# 0.10
if isinstance(box.metadata, Mapping):
    ...
```

A `frozendict` still raises on every change, so code that only reads the
metadata, or builds a new value from it as in
[How to attach metadata](attach-metadata.md#change-a-value-you-read),
needs no change.
