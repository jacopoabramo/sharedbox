---
icon: lucide/code
---

# Boxes

| Name | What it does |
| --- | --- |
| [`SharedBox`][sharedbox.SharedBox] | a record whose annotated [fields](../../explanation/glossary.md#field) live in a named shared-memory [segment](../../explanation/glossary.md#segment) |
| [`field`][sharedbox.field] | sets the options of one field of a `SharedBox` class, as `dataclasses.field` does |
| [`fields`][sharedbox.fields] | returns one `Field` per field of a `SharedBox` subclass or [box](../../explanation/glossary.md#box), in declaration order |
| [`Field`][sharedbox.Field] | options of one field of a `SharedBox` class, as `field` takes them |
| [`BoundedList`][sharedbox.BoundedList] | a list value that keeps the stored form of a list field, so reading and writing it copies bytes instead of converting every element |
| [`BoundedSet`][sharedbox.BoundedSet] | a set value that keeps the stored form of a set field, so reading and writing it copies bytes and a lookup compares the stored bytes of one key |
| [`BoundedDict`][sharedbox.BoundedDict] | a dict value that keeps the stored form of a dict field, so reading and writing it copies bytes and a lookup compares the stored bytes of one key |
| [`Timed`][sharedbox.Timed] | a value with its timestamp in nanoseconds, written in one go; `Timed.now` asks the write to stamp it |
| [`Call`][sharedbox.Call] | a field through which any process sends a typed request to the process that serves it |
| [`call`][sharedbox.call] | declares a `Call` field, which the box's constructor takes no value for |
| [`Attr`][sharedbox.Attr] | a field its owner publishes and every process reads: a value, and optionally a setpoint and a timestamp |
| [`attr`][sharedbox.attr] | declares an `Attr` field, which the box's constructor takes no value for |
| [`AttrValue`][sharedbox.AttrValue] | what `Attr.read` returns |
| [`RequestFuture`][sharedbox.RequestFuture] | the answer to one request, waited on with `result` or `await` |
| [`RequestServer`][sharedbox.RequestServer] | answers the requests of one call field; `Call.serve` returns it |
| [`Capacity`][sharedbox.Capacity] | maximum size of a field: bytes for text and bytes, elements for a collection. See [capacity](../../explanation/glossary.md#capacity) |
| [`segment_status`][sharedbox.segment_status] | says whether a box or stream exists under a name and whether its creator still runs |
| [`SegmentStatus`][sharedbox.SegmentStatus] | what `segment_status` found: `missing`, `running` or `creator_exited` |

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

::: sharedbox.Timed
    options:
      show_root_heading: true

::: sharedbox.Call
    options:
      show_root_heading: true

::: sharedbox.call
    options:
      show_root_heading: true

::: sharedbox.Attr
    options:
      show_root_heading: true

::: sharedbox.attr
    options:
      show_root_heading: true

::: sharedbox.AttrValue
    options:
      show_root_heading: true

::: sharedbox.RequestFuture
    options:
      show_root_heading: true

::: sharedbox.RequestServer
    options:
      show_root_heading: true

::: sharedbox.BoundedList
    options:
      show_root_heading: true
      inherited_members: true

::: sharedbox.BoundedSet
    options:
      show_root_heading: true
      inherited_members: true

::: sharedbox.BoundedDict
    options:
      show_root_heading: true
      inherited_members: true

::: sharedbox.Capacity
    options:
      show_root_heading: true

::: sharedbox.segment_status
    options:
      show_root_heading: true

::: sharedbox.SegmentStatus
    options:
      show_root_heading: true
