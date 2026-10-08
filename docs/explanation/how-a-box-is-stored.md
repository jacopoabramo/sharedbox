---
icon: lucide/lightbulb
---

# How a box is stored

When you write `motor.position = 10`, the number has to end up somewhere
every process can see, in a form every process understands. This page
explains where a [box](glossary.md#box) lives in memory, how its values are
stored, and how two processes make sure they agree on what the bytes mean.
[Segment layout](../reference/segment-layout.md) has every offset and step
in full.

## One named mapping per box

Every box lives in one block of memory that the operating system lets
several processes map at the same time, its
[segment](glossary.md#segment). The block has a name, so any process that
knows the name can open it, which is how a second process finds your
box.[^shm-open]

On Linux it is a POSIX shared memory object, `/dev/shm/sharedbox.<name>`,
created with mode `0600`, readable and writable by its owner
only.[^shm-open] On Windows it is a file mapping backed by the page file,
`Local\sharedbox.<name>`, which Windows deletes when the last process closes
it.[^create-file-mapping] The standard library's
`multiprocessing.shared_memory` creates the same kind of object on
Windows.[^shared-memory] Creation always asks for a new name, so a box
never attaches to a block that another program created first.
[Names](../reference/segment-layout.md#names) lists every object name.

## A fixed layout: header, field table, record

Inside the block, everything has a fixed place. Here is the memory of the
tutorial's `Motor` box, with its `int`, its `bool` and its 32-byte `str`,
after the second process set `position` to 10. The left column is the
address, the right one what is stored there; point at a part to read more:

```d2 title="The memory of a Motor box"
...@diagrams/style
grid-columns: 1
vertical-gap: 0
header: "header  0x000 - 0x07F" {
  shape: sql_table
  tooltip: Line 0, up to 0x03F, is written once when the box is created. Line 1, from 0x040, changes with every write and wait.
  "0x000": "magic  \"SHREDBX1\", written last"
  "0x008": "layout  2.0"
  "0x00C": "field_count  3"
  "0x00E": "waiter_slots  64"
  "0x010": "schema_hash"
  "0x018": "record_size  48"
  "0x01C": "record  0x6C0"
  "0x020": "tail  0x080"
  "0x024": "size  0x1000"
  "0x028": "create_id, creator start and pid"
  "0x03C": "types_size  0"
  "0x040": "seq  2, even: no write running"
  "0x048": "writer_pid  0"
  "0x04C": "wake_word, waiters"
  "0x058": "creator_pidns, then reserved"
}
fields: "field table  0x080 - 0x097" {
  shape: sql_table
  tooltip: One 8-byte entry per field in declaration order. Each gives the offset in the record and, in one u32, the kind code and the size. A process that opens the box copies this table once.
  "0x080": "position  +0x00, int, 8"
  "0x088": "enabled  +0x2C, bool, 1"
  "0x090": "label  +0x08, str, 32"
}
counts: "write counts  0x098 - 0x0AF" {
  shape: sql_table
  tooltip: One u64 per field, raised by every write to it, so a watcher can tell which fields changed.
  "0x098": "position  1"
  "0x0A0": "enabled  0"
  "0x0A8": "label  0"
}
slots: "waiter slots  0x0B0 - 0x6AF" {
  shape: sql_table
  tooltip: 64 slots of 24 bytes, one per thread waiting for changes. Each records the owner's start time, pid namespace and pid, and an interrupt flag. The space up to 0x6C0 is padding.
  "0x0B0": "slot 0  start, pidns, pid, interrupt"
  "0x0C8": "slot 1"
  "...": "slots 2 to 63"
  "0x6B0": "padding to a 64-byte boundary"
}
record: "record  0x6C0 - 0x6EF" {
  shape: sql_table
  tooltip: The field values, ordered by alignment, largest first. Every value is little-endian.
  "0x6C0": "position = 10 | 0A 00 00 00 00 00 00 00"
  "0x6C8": "label length = 6 | 06 00 00 00"
  "0x6CC": "label = \"x-axis\" | 78 2D 61 78 69 73, then unused"
  "0x6EC": "enabled = False | 00"
  "0x6ED": "padding to 48 bytes"
}
rest: "unused  0x6F0 - 0xFFF" {
  shape: sql_table
  tooltip: The mapping is rounded up to whole 4 KiB pages.
  "0x6F0": "rest of the first 4 KiB page"
}
```

[Layout](../reference/segment-layout.md#layout) gives every offset. The
values you pass to [`create`][sharedbox.SharedBox.create] are in the record
before the header's `magic` word is set, and a process that attaches waits
for that word, so it never sees a [field](glossary.md#field) before it
holds its starting value.

Fixed places might look rigid next to something like a dictionary kept in
shared memory, but they buy three things:

- Reading or writing a field is one copy of a known number of bytes to or
  from a known address. There is no structure to walk and nothing to
  rebalance.
- Values are stored as plain bytes that the native module converts. Nothing
  is ever unpickled. Unpickling runs code chosen by whoever wrote the
  bytes,[^pickle] so a box whose values were pickles would let any process
  that can write the block run code in every process that reads it.
- A process that opens the block with a different version of the class is
  refused: the [schema hash](glossary.md#schema-hash) in the header must
  match the hash the opener computes from its own class.

The record's position is stored as a distance from the start of the block,
not as an address, because each process maps the block at a different
address.

## How values are stored

Every value is turned into a fixed number of bytes: numbers packed the way
`struct` packs them, text as UTF-8. Reading turns the bytes back into a
value, and nothing stored is ever unpickled or run as code.
The table in [`SharedBox`][sharedbox.SharedBox] lists the stored size of
each field type, and [Layout](../reference/segment-layout.md#layout) the
bytes of each kind.

Because each value is copied into the record as bytes, a box can only hold
the types it knows how to store, listed in [Field types](field-types.md),
and not any Python object.
A field takes the room of its [capacity](glossary.md#capacity) whatever it
holds.
[`Capacity`][sharedbox.Capacity] goes inside `Annotated` because it belongs
to the stored type, not to the field's options.

Each field is an attribute of the class, and reading or writing that
attribute is what reaches into the record. A plain class attribute with the
same name in a subclass would hide it, so a subclass can't simply assign a
new default to an inherited field; it declares the field again with an
annotation instead (see [`field`][sharedbox.field]).

Two box objects on the same segment share one record, so they always hold
the same values, and comparing them by value would tell you nothing. So `==`
on two boxes is `True` only when both are the same Python object, just as
`is` would be.

## The schema hash

Imagine one process declares the field at offset 16 as a `float` and
another, running older code, as an `int`. The second would read the first
one's numbers as nonsense, with no error to warn it. The field table can't
catch that: it gives each field's offset, capacity and kind, but not which
field is called `position`, and only the classes know what the fields
mean. So when a process opens a block, something has to confirm that its
class agrees with the creator's about every byte.

That something is the [schema hash](glossary.md#schema-hash): a fingerprint
of the class's [identity](glossary.md#identity) and each field's name and
type, stored in the header by the process that creates the box.
[Schema identity](../reference/segment-layout.md#schema-identity) has the
exact text that is hashed and a worked example. A process that attaches
works out the hash from its own class and compares it with the header's
before it reads any field.

So anything that changes the meaning of the bytes changes the hash, and
[`attach`][sharedbox.SharedBox.attach] raises
[`SchemaMismatchError`][sharedbox.SchemaMismatchError] instead of reading
nonsense. That covers adding, removing, renaming or reordering a field,
changing its type or capacity, and changing the identity (which by default
happens when you move or rename the class). Changes that leave the bytes'
meaning alone don't affect it: a new method, a docstring, a different
default value.

It helps to know what the schema hash doesn't do:

- It is not a check of who created the block. Any process that can write
  the block can write any hash into the header. It protects against
  mistakes, such as an old and a new version of a program running at the
  same time, not against a hostile process. [Reading and
  writing](reading-and-writing.md#never-trust-the-shared-copy-of-the-layout)
  and the `0600` permissions deal with that.
- It does not cover changes in how sharedbox itself lays out a record
  between releases. The header's `layout_major` and `layout_minor` do.
- 8 bytes of SHA-256 give 2^64 possible values, so two different classes
  sharing a hash by accident is not a practical concern.

## Sources

[^shm-open]:
    Linux manual page `shm_open(3)`: named shared memory, the name as the
    way to open it, permission bits.
    <https://man7.org/linux/man-pages/man3/shm_open.3.html>

[^create-file-mapping]:
    Microsoft, `CreateFileMappingW`: a mapping backed by the paging file,
    freed when its last handle is closed.
    <https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createfilemappingw>

[^shared-memory]:
    CPython source, `Lib/multiprocessing/shared_memory.py` (the Windows
    branch uses a page-file-backed file mapping), and the documentation of
    `SharedMemory.close()` and `unlink()`.
    <https://github.com/python/cpython/blob/main/Lib/multiprocessing/shared_memory.py>,
    <https://docs.python.org/3/library/multiprocessing.shared_memory.html>

[^pickle]:
    Python documentation, `pickle`, the warning at the top of the page:
    unpickling can execute arbitrary code.
    <https://docs.python.org/3/library/pickle.html>
