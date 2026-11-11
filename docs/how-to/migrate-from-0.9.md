---
icon: lucide/arrow-up-circle
---

# How to migrate from 0.9

Code written for `sharedbox` 0.9 needs two changes before it runs on the
next release: a box class no longer takes fields from a base class that is
not a box class, and [metadata](../explanation/glossary.md#metadata) reads
back as a `frozendict` where 0.9 gave a `types.MappingProxyType`. This
guide shows the old and the new form of each.

## Declare a mixin's fields in the box class

A box class used to make a field of every annotated attribute of its base
classes, a plain mixin's included. It now takes fields only from itself and
from the bases that are box classes, as `dataclasses` takes them only from
bases that are dataclasses. An annotated attribute of a plain mixin
therefore stays an ordinary class attribute, so a class that needs it
stored declares it:

```python
class Counted:
    total: int = 0

# 0.9: total and count are both fields
class Sample(Counted, SharedBox):
    count: int = 0

# 0.10
class Sample(Counted, SharedBox):
    total: int = 0
    count: int = 0
```

The old form still defines the class, so it fails later, where the box is
created. In 0.9 `Sample(5, 1)` set `total` and `count`. The class now has
the single field `count`, so the same call raises `TypeError` for one value
too many. The fields are part of
the [schema hash](../explanation/glossary.md#schema-hash), so a process that
still runs 0.9 cannot attach to a box the new class created: update every
process that opens the box together.

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
