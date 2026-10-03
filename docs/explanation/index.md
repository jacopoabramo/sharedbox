---
icon: lucide/lightbulb
---

# Explanations

These pages explain how sharedbox stores a [box](glossary.md#box) in shared
memory, and why it is built that way. The byte layout and the protocols
themselves are specified in [Segment layout](../reference/segment-layout.md);
these pages give the reasons behind them and describe how the Python
extension uses
[`sharedbox.hpp`](https://github.com/jacopoabramo/sharedbox/blob/main/include/sharedbox/sharedbox.hpp).
The words they use are defined in the [glossary](glossary.md), which is
listed under Reference.

The Python side decides which [fields](glossary.md#field) a box has and
where each one lives. The C++ side owns the shared memory, converts values
to and from their stored bytes, and makes sure a reader never sees a
half-finished write.

- [When to use sharedbox](when-to-use-sharedbox.md): a box against an
  ordinary object shared between threads
- [How a box is stored](how-a-box-is-stored.md): the named mapping, its
  layout, and the [schema hash](glossary.md#schema-hash)
- [Field types](field-types.md): the types a field can hold and how each
  is stored
- [Reading and writing](reading-and-writing.md): the copied layout and the
  [sequence lock](glossary.md#sequence-lock)
- [Waiting for changes](waiting-for-changes.md):
  [waiter slots](glossary.md#waiter-slot), how changes reach callbacks,
  and what following costs
- [Reference fields](references.md): what a
  [reference field](glossary.md#reference-field) stores and why it can
  break
- [Closing and lifetime](closing-and-lifetime.md): closing a box other
  threads use, and when the memory goes away
- [Checking a process is alive](checking-a-process-is-alive.md): telling a
  running process from one that exited
- [How the module is built](how-the-module-is-built.md): one build line for
  three kinds of wheel
- [How fast a box is](performance.md): sharedbox timed against the
  standard library, in two charts
- [Limits](limits.md): platforms, file descriptors, threads, and what a box
  cannot hold
