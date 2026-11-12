---
icon: lucide/code
---

# Streams

| Name | What it does |
| --- | --- |
| [`SharedStream`][sharedbox.SharedStream] | a named ring of typed items in shared memory, sent by one process and received by up to `max_readers` readers |
| [`SharedSendStream`][sharedbox.SharedSendStream] | the one [sender](../../explanation/glossary.md#sender) of a [stream](../../explanation/glossary.md#stream), with blocking, non-blocking and asyncio sends |
| [`SendSlot`][sharedbox.SendSlot] | a slot reserved by `reserve`: its item, whose arrays are filled in place, and `send` |
| [`SharedReceiveStream`][sharedbox.SharedReceiveStream] | a [reader](../../explanation/glossary.md#reader) of a stream, in mode [lossless](../../explanation/glossary.md#lossless), [lossy](../../explanation/glossary.md#lossy) or [latest](../../explanation/glossary.md#latest) |
| [`ReceiveEvents`][sharedbox.ReceiveEvents] | the psygnal signal group of a reader: `received` for each item and `ended` once |
| [`SharedStreamStatistics`][sharedbox.SharedStreamStatistics] | a stream's state at one moment: what was sent, what the ring holds and who reads it |
| [`ReceiveStreamStatistics`][sharedbox.ReceiveStreamStatistics] | one open reader of a stream: its mode, position, lag and process |
| [`IterInto`][sharedbox.IterInto] | the iterator `iter_into` returns: each item read into the same arrays |
| [`Mode`][sharedbox.Mode], [`Start`][sharedbox.Start], [`OnFull`][sharedbox.OnFull] | the literal types of a reader's `mode` and `start` and of a sender's `on_full`, for annotations |

::: sharedbox.SharedStream
    options:
      show_root_heading: true

::: sharedbox.SharedSendStream
    options:
      show_root_heading: true

::: sharedbox.SendSlot
    options:
      show_root_heading: true
      inherited_members: true

::: sharedbox.SharedReceiveStream
    options:
      show_root_heading: true

::: sharedbox.ReceiveEvents
    options:
      show_root_heading: true

::: sharedbox.SharedStreamStatistics
    options:
      show_root_heading: true

::: sharedbox.ReceiveStreamStatistics
    options:
      show_root_heading: true

::: sharedbox.IterInto
    options:
      show_root_heading: true

::: sharedbox.Mode
    options:
      show_root_heading: true

::: sharedbox.Start
    options:
      show_root_heading: true

::: sharedbox.OnFull
    options:
      show_root_heading: true
