---
icon: lucide/lightbulb
---

# Limits

This page lists what sharedbox does not do, and the limits that come from
the operating system or from how a [box](glossary.md#box) is built.

## Platforms

sharedbox runs on Windows and Linux. macOS is not supported.

On Windows the [segment](glossary.md#segment) is freed with its last
[handle](glossary.md#handle), and [`unlink`][sharedbox.SharedBox.unlink]
does nothing. A `lock_timeout` can expire late by up to one timer tick; the
Notes of [`SharedBox`][sharedbox.SharedBox] give its length, and [Reading
and writing](reading-and-writing.md#waiting-for-the-lock) the measurements.

On Linux the segment is created with mode `0600`, so only the same user can
open it. Only `unlink` removes its name, and a segment that is never
unlinked stays until reboot (see [Closing and
lifetime](closing-and-lifetime.md#lifetime-the-same-rules-as-multiprocessingshared_memory)).

Releases up to 0.3.0rc0 named segments differently and used another
layout, so they and this release do not see each other's boxes. Releases
0.3.0 and 0.3.1 cannot open a box made with layout 2.0; this release opens
the boxes of 0.3.0 and 0.3.1.

## Open boxes and file descriptors

On Linux every open box keeps one file descriptor, so a process reaches the
default limit of 1024 (`ulimit -n`) at about 1000 open boxes. Box objects
made by reading a [reference field](glossary.md#reference-field), and the
handles that forwarding attaches, count too.

## Following boxes

[`follow`][sharedbox.BoxEvents.follow] takes, for each box it follows, one
[waiter slot](glossary.md#waiter-slot) in that box, and one handle and one
[watcher](glossary.md#watcher) thread in the following process. Following
64 chains of 3 boxes runs 192 threads, and a wide graph can use up the
waiter slots of a box. [Forwarding
costs](waiting-for-changes.md#forwarding-costs) has the measurements.

## What a box holds

A box holds the types listed in [Field types](field-types.md), and no
other objects. The number of fields and the size of a capacity are
limited: [`SharedBox`][sharedbox.SharedBox] and
[`Capacity`][sharedbox.Capacity] give the numbers. Also:

- A collection holds 1 to 1048576 elements.
- Types nest 16 levels deep.
- An enum or literal holds at most 65535 values, a flag 64 members and a
  union 255.
- An array has 1 to 8 dimensions.
- A record, arrays included, is less than 4 GiB.

## One lock per box

Writers take one lock per box and take turns, and readers never block
writers. [Reading and writing](reading-and-writing.md) explains why.
