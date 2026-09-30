---
icon: lucide/wrench
---

# How to change several fields at once

Assigning `point.x` and then `point.y` is two writes, and another process
can read the [box](../explanation/glossary.md#box) between them and see a
new `x` with an old `y`. Reading two [fields](../explanation/glossary.md#field) one after the
other has the
same problem. Write and read the fields together instead.

## 1. Write them with `update`

[`update`][sharedbox.SharedBox.update] takes the fields by name and writes
them under one lock:

```{.python}
--8<-- "docs/examples/change_several_fields_at_once.py:update"
```

A reader in any process sees either all of the new values or none of them.

## 2. Handle a value that is refused

`update` checks every value before it writes any, so a wrong one leaves the
box as it was:

```{.python}
--8<-- "docs/examples/change_several_fields_at_once.py:all-or-nothing"
```

## 3. Read them with `snapshot`

[`snapshot`][sharedbox.SharedBox.snapshot] returns every field in a dict,
all read at the same moment:

```{.python}
--8<-- "docs/examples/change_several_fields_at_once.py:snapshot"
```

## 4. Read the boxes a box refers to

A [reference field](../explanation/glossary.md#reference-field) appears in
a snapshot as a [`BoxRef`][sharedbox.BoxRef]. Pass `follow=True` to get the
snapshot of the box it refers to instead:

```{.python}
--8<-- "docs/examples/change_several_fields_at_once.py:follow"
```

Each box is read at its own moment, so the nested snapshot is not taken
together with the outer one.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/change_several_fields_at_once.py"
    ```
