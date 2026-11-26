---
icon: lucide/code
---

# Library authors

| Name | What it does |
| --- | --- |
| [`SupportsSharedBox`][sharedbox.SupportsSharedBox] | an object that hands its shared-memory [segment](../../explanation/glossary.md#segment) to other extensions |
| [`SupportsSharedStream`][sharedbox.SupportsSharedStream] | an object that hands a [stream](../../explanation/glossary.md#stream)'s segment to other extensions |
| [`get_include`][sharedbox.get_include] | returns the folder holding `sharedbox/sharedbox.hpp`, `sharedbox/sharedbox_c.h` and `sharedbox/sharedbox_c.cpp` |
| [`get_schema_hash`][sharedbox.get_schema_hash] | returns the [schema hash](../../explanation/glossary.md#schema-hash) of a box class, box, stream or stream item type, the value C and C++ code checks before reading |

To build an extension that takes a [box](../../explanation/glossary.md#box)
from Python, follow
[How to accept a box in a C++ extension](../../how-to/accept-a-box-in-cpp.md);
for a stream, follow
[How to accept a stream in a C++ extension](../../how-to/accept-a-stream-in-cpp.md).

::: sharedbox.SupportsSharedBox
    options:
      show_root_heading: true

::: sharedbox.SupportsSharedStream
    options:
      show_root_heading: true

::: sharedbox.get_include
    options:
      show_root_heading: true

::: sharedbox.get_schema_hash
    options:
      show_root_heading: true
