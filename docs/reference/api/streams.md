---
icon: lucide/code
---

# Streams

| Name | What it does |
| --- | --- |
| [`SharedStream`][sharedbox.SharedStream] | a named ring of typed items in shared memory, sent by one process and received by up to `max_readers` readers |
| [`StreamSender`][sharedbox.StreamSender] | the one [sender](../../explanation/glossary.md#sender) of a [stream](../../explanation/glossary.md#stream), with blocking, non-blocking and asyncio sends |
| [`StreamReader`][sharedbox.StreamReader] | a [reader](../../explanation/glossary.md#reader) of a stream, in mode [lossless](../../explanation/glossary.md#lossless), [lossy](../../explanation/glossary.md#lossy) or [latest](../../explanation/glossary.md#latest) |
| [`ReaderEvents`][sharedbox.ReaderEvents] | the psygnal signal group of a reader: `received` for each item and `ended` once |
| [`StreamStatistics`][sharedbox.StreamStatistics] | a stream's state at one moment: what was sent, what the ring holds and who reads it |
| [`ReaderStatistics`][sharedbox.ReaderStatistics] | one open reader of a stream: its mode, position, lag and process |

::: sharedbox.SharedStream
    options:
      show_root_heading: true

::: sharedbox.StreamSender
    options:
      show_root_heading: true

::: sharedbox.StreamReader
    options:
      show_root_heading: true

::: sharedbox.ReaderEvents
    options:
      show_root_heading: true

::: sharedbox.StreamStatistics
    options:
      show_root_heading: true

::: sharedbox.ReaderStatistics
    options:
      show_root_heading: true
