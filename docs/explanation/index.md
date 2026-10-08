---
icon: lucide/lightbulb
---

# Explanations

You can use `sharedbox` without reading any of these pages. Read them when
you want to know what happens inside a [box](glossary.md#box): how it is
kept in shared memory, how several processes can read and write it at once
without getting in each other's way, and why it was built that way. Every
word they use is defined in the [glossary](glossary.md).

The work is split between two halves. The Python side decides which
[fields](glossary.md#field) a box has and where each one sits. The C++ side,
[`sharedbox.hpp`](https://github.com/jacopoabramo/sharedbox/blob/main/include/sharedbox/sharedbox.hpp),
owns the shared memory, turns values into stored bytes and back, and makes
sure a reader never sees a half-finished write. These pages explain the
reasons; the exact byte layout and steps are written down in
[Segment layout](../reference/segment-layout.md).

- [When to use sharedbox](when-to-use-sharedbox.md): when a box is the
  right tool, and when an ordinary object shared between threads is
  simpler
- [How a box is stored](how-a-box-is-stored.md): what the shared memory
  holds, and how a process checks it agrees with the box's layout
- [Field types](field-types.md): the types a field can hold and how each
  is stored
- [Reading and writing](reading-and-writing.md): how a reader never sees
  half a write, through the [sequence lock](glossary.md#sequence-lock)
- [Waiting for changes](waiting-for-changes.md): how a change in one
  process reaches callbacks in another, and what following boxes costs
- [Reference fields](references.md): what a
  [reference field](glossary.md#reference-field) stores and why it can
  break
- [Closing and lifetime](closing-and-lifetime.md): what closing a box
  does, and when its memory goes away
- [Checking a process is alive](checking-a-process-is-alive.md): telling a
  running process from one that exited
- [How the module is built](how-the-module-is-built.md): how one build
  makes the three kinds of wheel
- [How fast a box is](performance.md): `sharedbox` timed against the
  standard library
- [Limits](limits.md): the platforms, how many boxes and threads you can
  have, and what a box can't hold
