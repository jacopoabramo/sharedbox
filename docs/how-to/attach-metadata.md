---
icon: lucide/wrench
---

# How to attach metadata to a box or stream

A [box](../explanation/glossary.md#box) or a
[stream](../explanation/glossary.md#stream) carries values, but not what
they mean, such as the unit of a weight or the calibration a reading was
taken with.
[Metadata](../explanation/glossary.md#metadata) carries that, in the same
[segment](../explanation/glossary.md#segment), so every process that opens
the box or stream reads it from there, in Python, C or C++. Each change
adds a new [metadata version](../explanation/glossary.md#metadata-version)
and keeps the older ones for a while, so a reader can tell which version
an item was sent under.

The `metadata` you pass to [`field`][sharedbox.field] is a different
thing: a mapping kept in each process's class and never stored in the
segment, from which `sharedbox` reads only its own keys, such as `"shape"`.

## Create a box with metadata

Give the type of the metadata as the type argument of `SharedBox`, and pass
the first value as `metadata` when you create the box:

```{.python}
--8<-- "docs/examples/attach_metadata.py:box"
```

Here the metadata is a `TypedDict`, so a type checker knows what
[`metadata`][sharedbox.SharedBox.metadata] holds and checks every value you
add. Without a type argument, as in `class Scale(SharedBox)`, the metadata
is a `Mapping[str, Any]`. `metadata` gives the value of the newest version,
or `None` for a box that has none, and
[`metadata_version`][sharedbox.SharedBox.metadata_version] gives its number,
counting from 1.

A value is `None`, a `bool`, an `int`, a `float`, a `str`, `bytes`, a list
or tuple, or a mapping with `str` keys, nested up to 32 levels;
[`add_metadata`][sharedbox.SharedBox.add_metadata] lists the limits. The
type argument must be a mapping type, but only a type checker holds you to
that: at run time a value that is not a mapping is stored all the same.
Calling the class takes the `metadata` keyword too, but a type checker
accepts it only in `create`, where it does not check the value.

## Add a version

Any process that has the box open adds a version with
[`add_metadata`][sharedbox.SharedBox.add_metadata], which returns the new
version's number:

```{.python}
--8<-- "docs/examples/attach_metadata.py:add"
```

Adding a version counts as one write to the box, so it takes the write
lock, but it changes no field. Every process that listens to the `metadata`
signal of the box's [`events`][sharedbox.SharedBox.events] gets the new
version's number, once for several adds that come close together:

```python
scale.events.metadata.connect(lambda version: print("calibration", version))
```

[`metadata_version_of`][sharedbox.SharedBox.metadata_version_of] reads an
older version for as long as the box keeps it. The box keeps the last
`metadata_versions` versions, 64 unless the class sets that keyword, or
fewer when they no longer fit in its room for metadata. Reading a version
it has let go raises [`MetadataGoneError`][sharedbox.MetadataGoneError].

## Leave room for metadata added later

A box created with `metadata` gets 65536 bytes of room for its metadata,
and one created without it gets none, so `add_metadata` on it raises
[`MetadataFullError`][sharedbox.MetadataFullError]. When the first version
comes after the box is created, give the class a `metadata_capacity`:

```{.python}
--8<-- "docs/examples/attach_metadata.py:capacity"
```

The capacity is the number of bytes all the kept versions share, 0 or at
least 24. One version can take up to a third of it, rounded down to a
multiple of 8, and a longer value raises `ValueError` and adds nothing. A
value is stored as CBOR, a compact binary form of JSON-like values, so
`{"unit": "kg", "offset": 0.0}` takes 19 bytes. `metadata_versions`, 2 to 65535, limits
how many versions the box keeps. A stream takes both as keywords of
[`create`][sharedbox.SharedStream.create].

## Attach metadata to a stream

A stream takes its first version as `metadata` in
[`create`][sharedbox.SharedStream.create], and its sender adds the next
ones with [`add_metadata`][sharedbox.SharedSendStream.add_metadata]. A
version applies from the next item the sender sends, and a reader's
[`metadata`][sharedbox.SharedReceiveStream.metadata] and
[`metadata_version`][sharedbox.SharedReceiveStream.metadata_version]
describe the item it received last, so the reader can tell which version
each item was sent under:

```{.python}
--8<-- "docs/examples/attach_metadata.py:stream"
```

The reader receives both readings after the second version was added, but
the first reading prints version 1, because that version was in force when
the reading was sent. Before its first item, a reader's `metadata_version`
is 0 and its `metadata` is `None`, and the same holds for an item sent
before the stream had any version. The stream's own
[`metadata`][sharedbox.SharedStream.metadata] gives the newest version, as
a box's does.

A type checker takes the stream's metadata type from `metadata_type`,
given to `create` as above or to `attach` in another process, as in
`SharedStream.attach(Reading, name, metadata_type=Calibration)`. Without
it the metadata is a `Mapping[str, Any]`, whatever `metadata` you pass.

A stream keeps every version that an item still in its ring was sent
under. An add that would let such a version go raises `MetadataFullError`
and adds nothing, and succeeds once the sender has sent enough further
items that none sent under that version is left in the ring.
Once a reader's last item has left the ring, though, its version can go,
and the reader's `metadata` then raises `MetadataGoneError`. A lossy or
latest reader meets this first, because the sender may overwrite its item
right after the reader receives it.

!!! warning "A callback on the main thread can see a later item's version"
    The reader's `metadata` follows the item the reader received last,
    which may be later than the item a callback was given. A callback of
    [`reader.events`][sharedbox.SharedReceiveStream.events] connected with
    `thread="main"` runs after the reader's thread has received its item,
    and that thread may have received the next one by then. Read
    `metadata` in a callback that runs on the reader's thread, and pass it
    on with the item.

!!! warning "sharedbox 0.7 cannot open a stream with metadata"
    `sharedbox` 0.7.0 and earlier refuse a stream created with `metadata`
    or a `metadata_capacity`, and raise
    [`SchemaMismatchError`][sharedbox.SchemaMismatchError] saying its
    header is corrupt. They open a box with metadata, and a stream created
    without it, and ignore the metadata. Create a stream without metadata
    while a process on 0.7 still opens it.

## Change a value you read

A value you read is a read-only view, because every read of a version
through the same box or stream shares one decoded copy. A mapping reads
back as a `types.MappingProxyType` and a list as a tuple, nested the same
way. A type checker still sees the metadata's type, though, so it accepts
a change that fails when it runs. To change a key, build a new value and
add it as the next version:

```{.python}
--8<-- "docs/examples/attach_metadata.py:read-only"
```

## Read metadata from C or C++

A C program reads a box's versions with `sbx_metadata_head` and
`sbx_read_metadata`, and adds one with `sbx_add_metadata`. In C++, `handle`
and `stream` read versions with `metadata_head` and `read_metadata`,
`handle` and `stream_sender` add them with `add_metadata`, and
`stream_reader::metadata_version_at` finds the version an item was sent
under; streams have no C calls. Each version is one CBOR value, which your
program decodes with a CBOR library of its own.
[Segment layout](../reference/segment-layout.md#metadata) lists the calls
and the CBOR a version may hold.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/attach_metadata.py"
    ```
