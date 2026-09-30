---
icon: lucide/lightbulb
---

# Reference fields

A [reference field](glossary.md#reference-field) lets one
[box](glossary.md#box) point at another. This page explains what the field
stores, how a process finds the class to open the other box with, and why a
reference can break. The rules for declaring, assigning and reading
reference fields are in [`SharedBox`][sharedbox.SharedBox].

## What a reference stores

The other box keeps its own [segment](glossary.md#segment), lock and
lifetime. The reference field stores only enough to open it again, from
any process:

- the box's name, which [`attach`][sharedbox.SharedBox.attach] takes;
- the [schema hash](glossary.md#schema-hash) of the box's own class, to
  find the class to attach it with;
- the box's [create id](glossary.md#create-id), to tell it apart from a
  box created later under the same name.

Because the outer box only points at the other box, each keeps its own
[sequence lock](glossary.md#sequence-lock). No write covers both boxes at
once, and closing or unlinking the outer box leaves the other alone.

## Finding the class

The stored schema hash says which class the box was created with, but a
process can only attach a box with a class it has defined itself. Each
process keeps a registry of its `SharedBox` classes by schema hash, and
adds a class when the class is defined. Reading a reference field looks
the stored hash up in that registry. A process that has not imported the
module defining the box's class finds nothing there and raises
[`UnknownBoxClassError`][sharedbox.UnknownBoxClassError].

The registry holds each class through a weak reference, so a class nothing
else uses, such as one defined inside a function, can be freed. That is
why the lookup takes the first class defined "that is still alive".

## Broken references

A reference stays in the segment while the box it names can change under
it: another process may unlink that box, or unlink it and create a new one
under the same name. The name alone cannot show this, which is why the
create id is stored. The first read of the field on a handle compares it
with the create id of the box under that name now and raises
[`BrokenReferenceError`][sharedbox.BrokenReferenceError] when the box is
gone or is a different one. After that the handle keeps its own mapping of
the box and returns it without checking again; see
[`SharedBox`][sharedbox.SharedBox].

These cases can arise at any time, from another process, long after
[`follow`][sharedbox.BoxEvents.follow] was called. So `follow` does not
raise for them: it logs a warning and forwards nothing until the field is
assigned another box.

## A class that refers to itself

A reference field may name the class being defined, which gives a chain of
boxes. A box of such a class cannot be created without an existing box of
it if the field is required, as with a dataclass, so a chain is declared
with an empty default:

```python
from __future__ import annotations

from sharedbox import SharedBox


class Node(SharedBox):
    value: int = 0
    next: Node | None = None
```

Without `from __future__ import annotations`, and before Python 3.14, the
annotation is quoted: `next: "Node | None" = None`.
