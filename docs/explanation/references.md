---
icon: lucide/lightbulb
---

# Reference fields

A [reference field](glossary.md#reference-field) lets one
[box](glossary.md#box) point at another, the way an attribute of one Python
object can hold another object. Pointing across processes is harder than
inside one, though: there are no shared Python objects to point at, only
names. This page explains what the field stores, how a process finds the
class to open the other box with, and how a reference can break. The rules
for declaring, assigning and reading reference fields are in
[`SharedBox`][sharedbox.SharedBox].

## What a reference stores

A reference field doesn't copy the other box into this one. The other box
keeps its own [segment](glossary.md#segment), lock and lifetime, and the
field stores just enough to open it again, from any process:

- the box's name, which [`attach`][sharedbox.SharedBox.attach] takes;
- the [schema hash](glossary.md#schema-hash) of the box's own class, to
  find the class to attach it with;
- the box's [create id](glossary.md#create-id), to tell it apart from a
  box created later under the same name.

Because the outer box only points at the other box, each keeps its own
[sequence lock](glossary.md#sequence-lock). That means no single write can
change both boxes at once, and closing or unlinking the outer box leaves the
other alone.

## Finding the class

The stored schema hash says which class the box was created with, but to
open the box a process needs that class itself, defined in its own code. So
each process keeps a list of the `SharedBox` classes it has defined, looked
up by schema hash, and a class joins the list as soon as it is defined.
Reading a reference field looks the stored hash up there.

!!! warning "Import the class before you read the reference"
    If a process hasn't imported the module that defines the other box's
    class, the lookup finds nothing and reading the field raises
    [`UnknownBoxClassError`][sharedbox.UnknownBoxClassError]. Import that
    module in every process that reads the reference.

The list holds each class weakly, so a class nothing else uses, such as one
defined inside a function, can still be freed. That's why the lookup takes
the first matching class "that is still alive".

## Broken references

A reference can outlive the box it names. Another process may unlink that
box, or unlink it and create a new one under the same name, and the name
alone can't show the difference. That's why the create id is stored too.
The first time a handle reads the field, it compares the stored create id
with that of the box under that name now, and raises
[`BrokenReferenceError`][sharedbox.BrokenReferenceError] if the box is gone
or is a different one. After that, the handle keeps the box it opened and
returns it without checking again; see [`SharedBox`][sharedbox.SharedBox].

A reference can break at any time, from another process, long after you
called [`follow`][sharedbox.BoxEvents.follow], when there's no call of yours
to raise an error into. So `follow` doesn't raise for a broken reference:
it logs a warning and forwards nothing until the field is pointed at
another box.

## A class that refers to itself

A reference field may name the very class it's in, which lets you build a
chain of boxes. If the field were required, you couldn't create the first
box, because it would need an existing box of the same class, just as with
a dataclass. So declare such a field with an empty default:

```python
from __future__ import annotations

from sharedbox import SharedBox


class Node(SharedBox):
    value: int = 0
    next: Node | None = None
```

Before Python 3.14, and without `from __future__ import annotations`, put
the annotation in quotes instead: `next: "Node | None" = None`.
