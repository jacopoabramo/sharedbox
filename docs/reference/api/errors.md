---
icon: lucide/code
---

# Errors

| Class | Base | Raised when |
| --- | --- | --- |
| [`SegmentExistsError`][sharedbox.SegmentExistsError] | `FileExistsError` | creating a [box](../../explanation/glossary.md#box) under a name that is already taken |
| [`SegmentNotFoundError`][sharedbox.SegmentNotFoundError] | `FileNotFoundError` | attaching to, or unlinking on Linux, a name with no [segment](../../explanation/glossary.md#segment), whether it holds a box or a stream; attaching to shared memory that does not become a box within 1 s |
| [`SchemaMismatchError`][sharedbox.SchemaMismatchError] | `TypeError` | attaching, or unpickling a box, with a class whose [identity](../../explanation/glossary.md#identity) or [fields](../../explanation/glossary.md#field) differ from the creator's; a segment of another core or layout major version; a segment whose magic this version does not know, which another version of `sharedbox` made or which is not a `sharedbox` segment; a segment with a field of a kind this version cannot read; unpickling after the box was created again |
| [`KindMismatchError`][sharedbox.KindMismatchError] | `SchemaMismatchError` | attaching a box to a name that holds a stream, or a stream to a name that holds a box |
| [`BoxClosedError`][sharedbox.BoxClosedError] | `ValueError` | using a box after `close()` |
| [`LockTimeoutError`][sharedbox.LockTimeoutError] | `TimeoutError` | a read or write waits for a write in progress for longer than `lock_timeout` |
| [`BrokenReferenceError`][sharedbox.BrokenReferenceError] | `LookupError` | reading a [reference field](../../explanation/glossary.md#reference-field), or `snapshot(follow=True)`, when the box it refers to was removed or created again since it was assigned |
| [`UnknownBoxClassError`][sharedbox.UnknownBoxClassError] | `TypeError` | reading a reference field, or `snapshot(follow=True)`, when no class this process has defined has the stored box's [schema hash](../../explanation/glossary.md#schema-hash) |
| [`WouldBlock`][sharedbox.WouldBlock] | `Exception` | a `_nowait` call on a [stream](../../explanation/glossary.md#stream) finds nothing to receive or no room to send |
| [`EndOfStream`][sharedbox.EndOfStream] | `Exception` | receiving from a stream whose sender closed or died after every item was received; asking for the sender of a stream that has ended |
| [`StreamBusyError`][sharedbox.StreamBusyError] | `RuntimeError` | asking for the sender of a stream while another live sender exists |
| [`StreamClosedError`][sharedbox.StreamClosedError] | `ValueError` | using a stream, sender or reader after `close()`, or waiting in one that is closed meanwhile |

::: sharedbox.BoxClosedError
    options:
      show_root_heading: true

::: sharedbox.BrokenReferenceError
    options:
      show_root_heading: true

::: sharedbox.KindMismatchError
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

::: sharedbox.WaiterSlotsFullError
    options:
      show_root_heading: true

::: sharedbox.EndOfStream
    options:
      show_root_heading: true

::: sharedbox.StreamBusyError
    options:
      show_root_heading: true

::: sharedbox.StreamClosedError
    options:
      show_root_heading: true

::: sharedbox.WouldBlock
    options:
      show_root_heading: true
