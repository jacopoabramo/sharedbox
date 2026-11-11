---
icon: lucide/arrow-up-circle
---

# How to migrate from 0.9

Code written for `sharedbox` 0.9 needs one change before it runs on the
next release: [metadata](../explanation/glossary.md#metadata) reads back as
a `frozendict` where 0.9 gave a `types.MappingProxyType`, so a test for
the old type no longer holds. This guide shows the old and the new form.

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
