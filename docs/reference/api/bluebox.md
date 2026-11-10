---
icon: lucide/code
---

# Bluesky documents

These names are imported from `sharedbox.bluebox`, which needs the
`bluebox` extra:

```python
from sharedbox.bluebox import DocumentReceiver, DocumentSender
```

| Name | What it does |
| --- | --- |
| [`DocumentSender`][sharedbox.bluebox.DocumentSender] | sends the [documents](../../explanation/glossary.md#document) of `bluesky` runs on a [channel](../../explanation/glossary.md#channel); subscribe it to a `RunEngine` |
| [`DocumentReceiver`][sharedbox.bluebox.DocumentReceiver] | receives a channel's documents in the order they were sent, in mode lossless, lossy or latest |
| [`ReceiverReleasedError`][sharedbox.bluebox.ReceiverReleasedError] | a receiver lost documents and was closed |
| [`LateJoinError`][sharedbox.bluebox.LateJoinError] | the run under way is too large for a receiver to join it |

::: sharedbox.bluebox.DocumentSender
    options:
      show_root_heading: true

::: sharedbox.bluebox.DocumentReceiver
    options:
      show_root_heading: true

::: sharedbox.bluebox.ReceiverReleasedError
    options:
      show_root_heading: true

::: sharedbox.bluebox.LateJoinError
    options:
      show_root_heading: true
