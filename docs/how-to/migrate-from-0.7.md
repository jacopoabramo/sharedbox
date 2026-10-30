---
icon: lucide/arrow-up-circle
---

# How to migrate from 0.7

Code written for `sharedbox` 0.7 needs two changes before it runs on the
next release: a [`Shape`][sharedbox.Shape] now takes its dimensions as one
tuple, and a box field can no longer take the name of a metadata member,
such as `metadata`. A third change concerns only type checkers: they refuse
`update(**values)` for some dictionaries that 0.7 accepted. This guide
shows the old and the new form of each.

## Pass the dimensions of a `Shape` as a tuple

`Shape` used to take each dimension as its own argument. It now takes a
single tuple, so a one-dimensional shape needs the trailing comma that makes
a tuple of one element:

```python
# 0.7
image: Annotated[numpy.ndarray, Shape(480, 640), DType("uint8")]
samples: Annotated[numpy.ndarray, Shape(1024), DType("float32")]

# 0.8
image: Annotated[numpy.ndarray, Shape((480, 640)), DType("uint8")]
samples: Annotated[numpy.ndarray, Shape((1024,)), DType("float32")]
```

The old form fails as the class is defined: `Shape(480, 640)` raises
`TypeError` because it passes two arguments, and `Shape(1024)` raises
`ValueError` because `1024` is not a tuple. Code that builds the dimensions
at run time passes the tuple it already has, `Shape(dims)`, in place of
`Shape(*dims)`.

## Rename a field called `metadata`

A box now has [metadata](../explanation/glossary.md#metadata) of its own,
which it reads through three new members: `metadata`,
`metadata_version` and `metadata_version_of`. A field or an
`InitVar` can no longer take one of those names, so give it another:

```python
# 0.7
class Parcel(SharedBox):
    metadata: Annotated[str, Capacity(64)] = ""

# 0.8
class Parcel(SharedBox):
    label: Annotated[str, Capacity(64)] = ""
```

The old form fails as the class is defined, with a `TypeError` that names
the field. Rename it wherever your code reads or assigns it as well, since
`parcel.metadata` now gives the box's metadata, usually `None`, and
assigning to it raises `AttributeError`. The field's name is part of the
[schema hash](../explanation/glossary.md#schema-hash), so a process that
still runs the old class cannot attach to a box the new class created:
update every process that opens the box together.

## Type a dictionary passed to `update` as `dict[str, Any]`

[`update`][sharedbox.SharedBox.update] now has a keyword `metadata` besides
the field names it takes. A type checker cannot tell which keys a `dict` holds,
so it assumes that `update(**values)` may fill `metadata`, and it refuses the
call when the values have a type the box's metadata type does not accept,
such as `str`. Type the dictionary as `dict[str, Any]`, or cast it where
you pass it:

```python
# 0.7
def restock(parcel: Parcel, values: dict[str, str]) -> None:
    parcel.update(**values)

# 0.8
def restock(parcel: Parcel, values: dict[str, Any]) -> None:
    parcel.update(**values)

def restock_cast(parcel: Parcel, values: dict[str, str]) -> None:
    parcel.update(**cast("dict[str, Any]", values))
```

Nothing changes when the program runs.
