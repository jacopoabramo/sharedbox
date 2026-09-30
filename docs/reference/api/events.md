---
icon: lucide/code
---

# Events

| Name | What it does |
| --- | --- |
| [`BoxEvents`][sharedbox.BoxEvents] | the psygnal signal group of a [box](../../explanation/glossary.md#box): one `(new, old)` signal per [field](../../explanation/glossary.md#field) |
| [`FieldWatch`][sharedbox.FieldWatch] | new values of one field, for `for` and `async for` |

::: sharedbox.BoxEvents
    options:
      show_root_heading: true
      members:
        - follow
        - unfollow
        - nested

::: sharedbox.FieldWatch
    options:
      show_root_heading: true
