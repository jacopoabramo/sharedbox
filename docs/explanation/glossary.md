---
icon: lucide/book-a
---

# Glossary

Each word on this page has its meaning written down once, here, and every
other page links to it the first time it uses the word. If a page uses a
word you don't know, its link brings you to this page.

Acronyms are explained in tooltips across the site: hover over a word with
a dotted underline to read what it stands for.

### Box

A box is a record that several processes share. You describe it with a
subclass of [`SharedBox`][sharedbox.SharedBox], and its [fields](#field)
live in one [segment](#segment) of [shared memory](#shared-memory), so every
process that opens the segment reads and writes the same values. In Python,
"box" also means the object you use to reach that record; several such
objects, in one process or in many, can be [handles](#handle) on the same
segment.

### Capacity

A capacity is how much room a field of varying size gets, since every field
has a fixed size. For `str`, `bytes`, `bytearray` and `Decimal` it is the
most bytes the value can take, and for a list, set or dict the most
elements. You set it with [`Capacity`][sharedbox.Capacity] inside
`Annotated`. For text it counts bytes, not characters, and the field takes
up all that room whatever it holds.

### Create id

A create id is a random number a [box](#box) gets when it is created, never
0, kept with the box. It tells two boxes apart that had the same name at
different times: a [reference field](#reference-field) and a pickled box
store it, so they can notice that the box they knew was removed and a new
one created under its name. See
[Reference fields](references.md#broken-references).

### Description

A description is the extra information a [segment](#segment) keeps for a
type whose name alone doesn't say how its values are laid out: an enum's
members, a record's members and their names, a list's capacity, or an
array's shape and element type.

### Field

A field is one value of a box, declared as a public annotation of a
`SharedBox` subclass with a type a box can store. Each field has a fixed
place and a fixed size in the [segment](#segment). See
[How a box is stored](how-a-box-is-stored.md).

### Handle

A handle is one way in to a [segment](#segment), held by one user: a box
object in Python, or the handle in the capsule that
[`__sharedbox_box__`][sharedbox.SharedBox.__sharedbox_box__] gives a C or
C++ library. Each handle has the segment mapped into its process on its
own, so closing one handle leaves the others working. See
[Closing and lifetime](closing-and-lifetime.md).

### Identity

The identity of a `SharedBox` class is the string that names the class the
same way in every process. It is the class's `module.qualname`, unless the
class sets the `identity` keyword. The box's default name and its
[schema hash](#schema-hash) are both worked out from it.

### Latest

Latest is a [reader](#reader) mode in which the reader always receives the
newest item of the [stream](#stream) and skips the rest. See
[`reader`][sharedbox.SharedStream.reader].

### Lossless

Lossless is a [reader](#reader) mode in which the reader receives every item
of the [stream](#stream). A [sender](#sender) that is a full ring ahead of a
lossless reader waits for it. See [`reader`][sharedbox.SharedStream.reader].

### Lossy

Lossy is a [reader](#reader) mode in which the reader skips the items the
[sender](#sender) overwrote while it was busy, and counts them in
[`missed`][sharedbox.StreamReader.missed]. See
[`reader`][sharedbox.SharedStream.reader].

### Reader

A reader is an object that receives the items of a [stream](#stream) in one
process. Each reader has its own position in the stream and a mode:
[lossless](#lossless), [lossy](#lossy) or [latest](#latest). See
[`StreamReader`][sharedbox.StreamReader].

### Reference field

A reference field is a [field](#field) annotated with another `SharedBox`
subclass, or with that class `| None`, so one box can point at another. It
stores which box it points at, not that box's values: the box's name,
[schema hash](#schema-hash) and [create id](#create-id). The other box keeps
its own segment, lock and lifetime. See [Reference fields](references.md).

### Schema hash

A schema hash is a short fingerprint of a box's layout: a number worked
out from the class's [identity](#identity) and each field's name, type and
capacity (the first 8 bytes of a SHA-256 hash of them). The
[segment](#segment) keeps it, and a process that attaches works it out
again from its own class. If the two differ, the classes don't describe the
same layout, and the attach is refused instead of reading the wrong bytes.
See [How a box is stored](how-a-box-is-stored.md#the-schema-hash).

### Segment

A segment is the block of [shared memory](#shared-memory) that holds one
[box](#box). Besides the values, it holds a header, a table of the fields
and the [waiter slots](#waiter-slot). It has a name, `SBX:<name>`, by
which any process can open it. See
[How a box is stored](how-a-box-is-stored.md).

### Sender

A sender is the object that sends the items of a [stream](#stream). A stream
has one at a time, and closing it ends the stream. See
[`StreamSender`][sharedbox.StreamSender].

### Sequence lock

The sequence lock is how a [box](#box) keeps readers from seeing half a
write. It is a counter in the segment that is even when no write is running
and odd while one is. A writer makes the counter odd, copies its values,
and makes it even again. A reader doesn't lock anything: it copies, and if
the counter moved in the meantime, it copies again. See
[Reading and writing](reading-and-writing.md).

### Shared memory

Shared memory is memory the operating system lets several processes use at
the same time, so a value one process writes there is immediately there for
the others to read, without being sent. Each [box](#box) is kept in a block
of it, its [segment](#segment).

### Stream

A stream is a series of items that one process sends and up to
`max_readers` [readers](#reader) receive. A ring in a [segment](#segment) of
[shared memory](#shared-memory) holds the last `capacity` items. See
[`SharedStream`][sharedbox.SharedStream].

### Waiter slot

A waiter slot is an entry in a [segment](#segment) that a waiting thread
holds, so that a write knows whom to wake up. The thread is a box's
[watcher](#watcher), or a C++ program that called `register_waiter`. The
slot records which process holds it, so a slot left behind by a process
that crashed can be freed again. A box has a fixed number of slots, shared
by every process. See [Waiting for changes](waiting-for-changes.md).

### Watcher

A watcher is a background thread of a box [handle](#handle) that waits for
writes from any process. It runs the callbacks you connect to
[`events`][sharedbox.SharedBox.events] and delivers the values that
[`watch`][sharedbox.SharedBox.watch] gives you, and it holds one
[waiter slot](#waiter-slot) while it waits. See
[Waiting for changes](waiting-for-changes.md).
