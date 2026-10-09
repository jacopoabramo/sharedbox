---
icon: lucide/lightbulb
---

# Limits

Before you build on `sharedbox`, it's worth knowing where it stops. This
page lists what it doesn't do, and the limits that come from the operating
system or from how a [box](glossary.md#box) is built.

## Platforms

`sharedbox` runs on Windows and Linux. macOS is not supported.

On Windows the [segment](glossary.md#segment) goes away when its last
[handle](glossary.md#handle) is closed, so
[`unlink`][sharedbox.SharedBox.unlink] has nothing to do. A `lock_timeout`
can also run out late, by up to one tick of the system timer; the Notes of
[`SharedBox`][sharedbox.SharedBox] give how long that is, and
[Reading and writing](reading-and-writing.md#waiting-for-the-lock) the
measurements.

On Linux the segment is created with mode `0600`, so only processes of the
same user can open it. Only `unlink` removes its name, so a segment nobody
unlinks stays until the machine restarts (see
[Closing and lifetime](closing-and-lifetime.md#lifetime-the-same-rules-as-multiprocessingshared_memory)).

If processes running different releases of `sharedbox` share boxes, check
which ones can talk to each other. Releases up to 0.5 named segments
`sharedbox.<name>`, and from 0.6.0 on they are `SBX:<name>`, so the two
groups don't see each other's boxes at all. Releases from 0.6 open each
other's boxes as long as both the core major version and the box layout
major version are the same.

## Open boxes and file descriptors

On Linux every open box keeps one file descriptor, and a process can have
1024 of those by default (`ulimit -n`), so you run out at about 1000 open
boxes. Boxes opened for you by reading a
[reference field](glossary.md#reference-field), and the handles that
following opens, count too.

## Following boxes

Following adds up quickly. For each box it follows,
[`follow`][sharedbox.BoxEvents.follow] takes one
[waiter slot](glossary.md#waiter-slot) in that box, and one handle and one
[watcher](glossary.md#watcher) thread in your process. Following 64 chains
of 3 boxes runs 192 threads, and a wide graph can use up all the waiter
slots of a box. [Forwarding
costs](waiting-for-changes.md#forwarding-costs) has the measurements.

## What a box holds

A box holds the types listed in [Field types](field-types.md), and no
other objects. How many fields a box has and how big a capacity can be are
limited too; [`SharedBox`][sharedbox.SharedBox] and
[`Capacity`][sharedbox.Capacity] give the numbers. The other limits are:

- A collection holds 1 to 1048576 elements.
- Types nest 16 levels deep.
- An enum or literal holds at most 65535 values, a flag 64 members and a
  union 255.
- An array has 1 to 8 dimensions.
- A record, arrays included, is less than 4 GiB.

## One lock per box

All writers of a box share one lock and take turns, while readers never
hold up a writer. In practice writers rarely wait for each other, because a
write holds the lock only for the few nanoseconds it takes to copy the
value. [Reading and writing](reading-and-writing.md) explains how this
works.
