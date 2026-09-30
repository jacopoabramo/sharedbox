---
icon: lucide/compass
---

# How-to Guides

Each guide gets one task done, step by step.

### Install

- [How to install sharedbox](install-sharedbox.md): the wheels, the
  platforms and the `benchmarks` extra

### Use a box

Learn how to name, fill, share and remove a
[box](../explanation/glossary.md#box).

- [How to name a box](name-a-box.md): let the class name it, or choose the
  name
- [How to store text and bytes](store-text-and-bytes.md): pick a
  capacity and handle values that do not fit
- [How to set defaults and check values](set-defaults-and-check-values.md):
  defaults, keyword-only fields and `__post_init__`
- [How to change several fields at once](change-several-fields-at-once.md):
  write with `update` and read with `snapshot`
- [How to send a box to another process](send-a-box-to-another-process.md):
  as an argument, by name, or to a pool
- [How to clean up segments](clean-up-segments.md): close, unlink, and
  recover after a crash
- [How to follow a whole reference graph](follow-a-whole-reference-graph.md):
  one callback for changes in every box a box refers to

### C and C++

Read and write a box from compiled code.

- [How to accept a box in a C++ extension](accept-a-box-in-cpp.md): take
  the box a Python caller passes, through `sharedbox.hpp`
- [How to accept a box in a C extension](accept-a-box-in-c.md): the same
  through `sharedbox_c.h`
- [How to open a box from a program](open-a-box-from-a-program.md): open
  a box by name from a program with no Python

### Measure

- [How to run the benchmarks](run-benchmarks.md): time sharedbox on your
  own machine with `benchbox`

To change sharedbox itself, see [Contributing](contribute.md).
