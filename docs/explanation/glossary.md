---
icon: lucide/book-a
---

# Glossary

This glossary explains the terms used across the documentation. Each term
is defined once, here, and other pages link to it the first time they use
it.

Acronyms also appear as tooltips across the site: hover a dotted-underlined
word to read it.

### Box

A box is a record whose [fields](#field) live in one
[segment](#segment) of shared memory, described by a subclass of
[`SharedBox`][sharedbox.SharedBox]. Every process that opens the segment
reads and writes the same values. In Python, a box is also the object that
gives access to the record, and several such objects, in one process or
many, can be [handles](#handle) on the same segment.

### Capacity

The capacity of a `str` or `bytes` [field](#field) is the largest number of
bytes it can hold, set with [`Capacity`][sharedbox.Capacity] inside
`Annotated`. It counts bytes, not characters. The field takes that much
room in the record whatever it holds.

### Create id

A create id is a random number drawn when a [box](#box) is created and kept
in its header; it is never 0. A [reference field](#reference-field) and a
pickled box store it, so a process can tell the box from one created later
under the same name. See [Reference fields](references.md#broken-references).

### Field

A field is a public annotation of a `SharedBox` subclass with one of the
types a box can store. Each field has a fixed place and a fixed size in the
record of the [segment](#segment). See
[How a box is stored](how-a-box-is-stored.md).

### Handle

A handle is one open mapping of a [segment](#segment): a box object in
Python, or the capsule handle that
[`__sharedbox_box__`][sharedbox.SharedBox.__sharedbox_box__] gives a C or
C++ library. Closing a handle detaches only that handle. See
[Closing and lifetime](closing-and-lifetime.md).

### Identity

The identity of a `SharedBox` class is the string that names it across
processes: its `module.qualname`, unless the class sets the `identity`
class keyword. It
enters the [schema hash](#schema-hash), and names the [box](#box) when the
class sets no `name`.

### Reference field

A reference field is a [field](#field) annotated with another `SharedBox`
subclass, or with that class `| None`. It stores which box it refers to:
the box's name, [schema hash](#schema-hash) and [create id](#create-id).
The other box keeps its own segment, lock and lifetime. See
[Reference fields](references.md).

### Schema hash

The schema hash is the first 8 bytes of SHA-256 over a class's
[identity](#identity) and each field's name, kind and capacity. It is kept
in the header of the [segment](#segment), and a process that attaches must
compute the same value from its own class. See
[How a box is stored](how-a-box-is-stored.md#the-schema-hash).

### Segment

A segment is the block of shared memory that holds one [box](#box): a
header, a table of the fields, the [waiter slots](#waiter-slot) and the
record of values. It has a name, `sharedbox.<name>`, by which any process
can open it. See [How a box is stored](how-a-box-is-stored.md).

### Sequence lock

The sequence lock is the write lock of a [box](#box): a counter in the
header that is even when no write runs and odd while one does. A writer
makes the counter odd, copies, and makes it even again. A reader takes no
lock: it copies and tries again if the counter moved. See
[Reading and writing](reading-and-writing.md).

### Waiter slot

A waiter slot is an entry in a [segment](#segment) that a waiting thread
holds, so a write knows whom to wake: a box's [watcher](#watcher), or a C++
program that called `register_waiter`. It records its owner's pid, start
time and pid namespace. A box has a fixed number of slots, shared by every
process. See [Waiting for changes](waiting-for-changes.md).

### Watcher

A watcher is the background thread of a box [handle](#handle) that waits
for writes from any process. It runs the callbacks of
[`events`][sharedbox.SharedBox.events] and serves the iterators of
[`watch`][sharedbox.SharedBox.watch], and holds one
[waiter slot](#waiter-slot) while it waits. See
[Waiting for changes](waiting-for-changes.md).
