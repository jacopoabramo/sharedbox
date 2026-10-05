---
icon: lucide/wrench
---

# How to store lists, sets and dicts

A `list`, `tuple[T, ...]`, `set`, `frozenset` or `dict`
[field](../explanation/glossary.md#field), or one annotated with
`Sequence`, `Set` or `Mapping` from `collections.abc`, holds up to a fixed
number of elements. The [box](../explanation/glossary.md#box) sets aside
room for all of them when it is created.

## 1. Declare the field with a capacity in elements

[`Capacity`][sharedbox.Capacity] on a collection counts elements. An
element that needs a capacity of its own, such as a `str`, gets it inside.
Set elements and dict keys must have a type that is hashable when read
back: a `list`, `set`, `dict`, `bytearray`, `TypedDict` or record class
that is not frozen raises `TypeError` when the class is defined.

```{.python}
--8<-- "docs/examples/store_collections.py:declare"
```

## 2. Write and read the collection

```{.python}
--8<-- "docs/examples/store_collections.py:write"
```

A read returns a new collection of the declared type; a `Sequence` reads
back as a `list`, a `Set` as a `set` and a `Mapping` as a `dict`. A set is
stored in its iteration order and a dict in insertion order.

## 3. Add an element

Changing a collection read from the box changes only that copy. Build the
new collection and assign it:

```{.python}
--8<-- "docs/examples/store_collections.py:add"
```

Annotating the field with `tuple[T, ...]` or `Sequence[T]` lets a type
checker point out an `append` that would change only the copy.

## 4. Handle a collection that is too large

```{.python}
--8<-- "docs/examples/store_collections.py:too-many"
```

The field keeps its old value.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/store_collections.py"
    ```
