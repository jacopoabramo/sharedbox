---
icon: lucide/wrench
---

# How to store lists, sets and dicts

A [field](../explanation/glossary.md#field) can hold a `list`, a `tuple[T, ...]`, a `set`, a
`frozenset` or a `dict`, or anything you annotate as `Sequence`, `Set` or
`Mapping` from `collections.abc`. Because the [box](../explanation/glossary.md#box) sets aside room
for the field when it is created, you say up front how many elements it can
hold at most.

## 1. Declare the field with a capacity in elements

On a collection, [`Capacity`][sharedbox.Capacity] counts elements, not
bytes. If the elements need a capacity of their own, such as text, put it on
the element type inside:

```{.python}
--8<-- "docs/examples/store_collections.py:declare"
```

Set elements and dict keys are read back into a new set or dict, so their
type has to be hashable, meaning Python can use it as a set element or dict
key. A `list`, `set`, `dict`, `bytearray`, `TypedDict`
or record class that isn't frozen can't be a set element or a dict key, and
the box class raises `TypeError` when you define it.

## 2. Write and read the collection

You assign and read a collection field like any other:

```{.python}
--8<-- "docs/examples/store_collections.py:write"
```

Every read gives you a new collection of the declared type. A field
annotated as `Sequence` comes back as a `list`, a `Set` as a `set` and a
`Mapping` as a `dict`. Order is kept: a set in the order you iterated it, a
dict in insertion order.

## 3. Add an element

A collection you read is a copy, so appending to it changes nothing in the
box. Build the new collection and assign it back:

```{.python}
--8<-- "docs/examples/store_collections.py:add"
```

If you annotate the field as `tuple[T, ...]` or `Sequence[T]`, a type
checker will point out an `append` that would only change the copy.

## 4. Handle a collection that is too large

Assigning more elements than the capacity raises `ValueError`, and the field
keeps its old value:

```{.python}
--8<-- "docs/examples/store_collections.py:too-many"
```

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/store_collections.py"
    ```
