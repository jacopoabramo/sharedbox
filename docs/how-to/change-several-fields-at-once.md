---
icon: lucide/wrench
---

# How to change several fields at once

When you assign `point.x` and then `point.y`, that's two writes, and another
process can read the [box](../explanation/glossary.md#box) in between and see a new `x` with an old
`y`. Reading two [fields](../explanation/glossary.md#field) one after the other has the same
problem. This guide shows how to write and read several fields together, so
nobody sees a half-finished change.

## 1. Write them with `update`

[`update`][sharedbox.SharedBox.update] takes the fields by name and writes
them all in one go:

```{.python}
--8<-- "docs/examples/change_several_fields_at_once.py:update"
```

A reader in any process then sees either all of the new values or none of
them.

## 2. Handle a value that is refused

`update` checks every value before it writes any of them, so if one is
wrong, nothing changes:

```{.python}
--8<-- "docs/examples/change_several_fields_at_once.py:all-or-nothing"
```

## 3. Read them with `snapshot`

[`snapshot`][sharedbox.SharedBox.snapshot] reads every field at the same
moment and gives you them in a dict:

```{.python}
--8<-- "docs/examples/change_several_fields_at_once.py:snapshot"
```

## 4. Read the boxes a box refers to

A [reference field](../explanation/glossary.md#reference-field) shows up in a snapshot as a
[`BoxRef`][sharedbox.BoxRef], which says which box it points at. Pass
`follow=True` to get the snapshot of that box instead:

```{.python}
--8<-- "docs/examples/change_several_fields_at_once.py:follow"
```

Each box is read at its own moment, though, so the nested snapshot isn't
taken at the same instant as the outer one.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/change_several_fields_at_once.py"
    ```
