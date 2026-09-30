---
icon: lucide/code
---

# Errors

| Class | Base | Raised when |
| --- | --- | --- |
| [`SegmentExistsError`][sharedbox.SegmentExistsError] | `FileExistsError` | creating a [box](../../explanation/glossary.md#box) under a name that is already taken |
| [`SegmentNotFoundError`][sharedbox.SegmentNotFoundError] | `FileNotFoundError` | attaching to, or unlinking on Linux, a name with no [segment](../../explanation/glossary.md#segment); attaching to shared memory that does not become a box within 1 s |
| [`SchemaMismatchError`][sharedbox.SchemaMismatchError] | `TypeError` | attaching, or unpickling a box, with a class whose [identity](../../explanation/glossary.md#identity) or [fields](../../explanation/glossary.md#field) differ from the creator's; a segment of another layout major version; a segment with a field of a kind this version cannot read; unpickling after the box was created again |
| [`BoxClosedError`][sharedbox.BoxClosedError] | `ValueError` | using a box after `close()` |
| [`LockTimeoutError`][sharedbox.LockTimeoutError] | `TimeoutError` | a read or write waits for a write in progress for longer than `lock_timeout` |
| [`BrokenReferenceError`][sharedbox.BrokenReferenceError] | `LookupError` | reading a [reference field](../../explanation/glossary.md#reference-field), or `snapshot(follow=True)`, when the box it refers to was removed or created again since it was assigned |
| [`UnknownBoxClassError`][sharedbox.UnknownBoxClassError] | `TypeError` | reading a reference field, or `snapshot(follow=True)`, when no class this process has defined has the stored box's [schema hash](../../explanation/glossary.md#schema-hash) |

::: sharedbox.BoxClosedError
    options:
      show_root_heading: true

::: sharedbox.BrokenReferenceError
    options:
      show_root_heading: true

::: sharedbox.LockTimeoutError
    options:
      show_root_heading: true

::: sharedbox.SchemaMismatchError
    options:
      show_root_heading: true

::: sharedbox.SegmentExistsError
    options:
      show_root_heading: true

::: sharedbox.SegmentNotFoundError
    options:
      show_root_heading: true

::: sharedbox.UnknownBoxClassError
    options:
      show_root_heading: true
