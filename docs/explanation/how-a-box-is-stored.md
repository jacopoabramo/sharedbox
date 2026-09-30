---
icon: lucide/lightbulb
---

# How a box is stored

This page describes where a [box](glossary.md#box) lives in memory, how its
values are stored, and how two processes check that they agree on what the
bytes mean. [Segment layout](../reference/segment-layout.md) gives every
offset and protocol.

## One named mapping per box

Every box lives in one block of memory that the operating system lets
several processes map at the same time, its
[segment](glossary.md#segment). The block has a name, and any process that
knows the name can open it.[^shm-open]

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

The mapping starts with a 128-byte header. Then come a table with one entry
per [field](glossary.md#field), one write count per field, the
[waiter slots](glossary.md#waiter-slot), and the record that holds the
field values, 64-byte aligned. [Layout](../reference/segment-layout.md#layout)
gives every offset.

Each field has a fixed place and a fixed size in the record. Fields are
packed by descending alignment (the 8-byte `int` and `float` fields and
reference fields first, then `str` and `bytes`, then `bool`), not
declaration order. The values passed to
[`create`][sharedbox.SharedBox.create] are written into the record before
the header's `magic` word is set, so an attaching process never sees a
record before every field holds its starting value.

sharedbox uses fixed places instead of something more flexible, such as a
dictionary stored in shared memory, for three reasons:

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

The record's position is stored as an offset rather than as an address,
because each process maps the block at a different address.

## How values are stored

Values are stored as fixed-size bytes: numbers packed the way `struct`
packs them, text as UTF-8. Stored bytes are never unpickled or executed.
The table in [`SharedBox`][sharedbox.SharedBox] lists the stored size of
each field type, and [Layout](../reference/segment-layout.md#layout) the
bytes of each kind.

Because a value is copied into the record, a box holds only the stored
types: no lists, dicts or other objects. A `str` or `bytes` field takes the
room of its [capacity](glossary.md#capacity) whatever it holds.
[`Capacity`][sharedbox.Capacity] goes inside `Annotated` because it belongs
to the stored type, not to the field's options.

Each field is an attribute of the class that reads and writes the record.
A plain class attribute with the same name in a subclass would hide that
attribute, so a subclass cannot set one on an inherited field; it declares
the field again with an annotation instead (see
[`field`][sharedbox.field]).

Two box objects on the same segment share one record, so they always hold
the same values. Comparing boxes by their values would therefore say
nothing, and boxes compare equal only when they are the same object.

## The schema hash

The field table tells the C++ side each field's offset, capacity and kind,
but nothing in it says which field is called `position`, or whether the
attaching class declares the field at offset 16 as an `int` or a `float`.
Only the class knows that. So when two processes open the same block,
something has to confirm they agree on the meaning of every byte;
otherwise one process would read another's `float` as an `int` and get
wrong values with no error.

The schema hash is the first 8 bytes of SHA-256 over the class's
[identity](glossary.md#identity) and each field's `name:kind:capacity`.
[Schema identity](../reference/segment-layout.md#schema-identity) has the
exact text and a test vector. The identity is `module.qualname` unless the
class sets `identity=`. The attaching process compares the header's hash
with its own class's after the layout checks and before it reads any field.

Anything that changes the meaning of the bytes changes the hash and makes
[`attach`][sharedbox.SharedBox.attach] raise
[`SchemaMismatchError`][sharedbox.SchemaMismatchError]: a field added,
removed, renamed, reordered or given another type or capacity, and the
identity changed (by default, the class moved to another module or
renamed). A change that leaves the bytes' meaning alone does not: a new
method, a docstring, a default value.

What the schema hash is not:

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
