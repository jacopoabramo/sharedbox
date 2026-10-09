---
icon: lucide/code
---

# Events

| Name | What it does |
| --- | --- |
| [`BoxEvents`][sharedbox.BoxEvents] | the psygnal signal group of a [box](../../explanation/glossary.md#box): one `(new, old)` signal per [field](../../explanation/glossary.md#field) |
| [`FieldFuture`][sharedbox.FieldFuture] | a `concurrent.futures.Future` of the next write to one field |
| [`FieldWatch`][sharedbox.FieldWatch] | new values of one field, for `for`, `async for` or `future()` |

::: sharedbox.BoxEvents
    options:
      show_root_heading: true
      members:
        - follow
        - unfollow
        - nested

::: sharedbox.FieldFuture
    options:
      show_root_heading: true
      members:
        - version

::: sharedbox.FieldWatch
    options:
      show_root_heading: true
