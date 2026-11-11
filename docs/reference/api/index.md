---
icon: lucide/code
---

# API

Everything on these pages is imported from `sharedbox` itself:

```python
from sharedbox import SharedBox, Capacity, field
```

| Page | Names |
| --- | --- |
| [Boxes](box.md) | `SharedBox`, `field`, `fields`, `Field`, `Timed`, `Attr`, `attr`, `AttrValue`, `RequestFuture`, `RequestServer`, `Capacity`, `segment_status`, `SegmentStatus` |
| [Arrays](arrays.md) | `Shape`, `DType`, `SupportsDLPack`, `register_array_type` |
| [Events](events.md) | `BoxEvents`, `FieldFuture`, `FieldWatch` |
| [Streams](streams.md) | `SharedStream`, `SharedSendStream`, `SendSlot`, `SharedReceiveStream`, `ReceiveEvents`, `SharedStreamStatistics`, `ReceiveStreamStatistics` |
| [References](references.md) | `BoxRef` |
| [Bluesky documents](bluebox.md) | `DocumentSender`, `DocumentReceiver`, `ReceiverReleasedError`, `LateJoinError`, imported from `sharedbox.bluebox` |
| [Errors](errors.md) | every exception `sharedbox` raises |
| [Library authors](library-authors.md) | `SupportsSharedBox`, `get_include` |
