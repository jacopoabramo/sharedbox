---
icon: lucide/code
---

# Boxes

| Name | What it does |
| --- | --- |
| [`SharedBox`][sharedbox.SharedBox] | a record whose annotated fields live in a named shared-memory segment |
| [`field`][sharedbox.field] | sets the options of one field of a `SharedBox` class, as `dataclasses.field` does |
| [`fields`][sharedbox.fields] | returns one `Field` per field of a `SharedBox` subclass or box, in declaration order |
| [`Field`][sharedbox.Field] | options of one field of a `SharedBox` class, as `field` takes them |
| [`Capacity`][sharedbox.Capacity] | maximum encoded size of a `str` or `bytes` field |

::: sharedbox.SharedBox
    options:
      show_root_heading: true

::: sharedbox.field
    options:
      show_root_heading: true

::: sharedbox.fields
    options:
      show_root_heading: true

::: sharedbox.Field
    options:
      show_root_heading: true

::: sharedbox.Capacity
    options:
      show_root_heading: true
