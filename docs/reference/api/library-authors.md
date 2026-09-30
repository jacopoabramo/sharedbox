---
icon: lucide/code
---

# Library authors

| Name | What it does |
| --- | --- |
| [`SupportsSharedBox`][sharedbox.SupportsSharedBox] | an object that hands its shared-memory segment to other extensions |
| [`get_include`][sharedbox.get_include] | returns the folder holding `sharedbox/sharedbox.hpp`, `sharedbox/sharedbox_c.h` and `sharedbox/sharedbox_c.cpp` |

To build an extension that takes a [box](../../explanation/glossary.md#box)
from Python, follow
[How to accept a box in a C++ extension](../../how-to/accept-a-box-in-cpp.md).

::: sharedbox.SupportsSharedBox
    options:
      show_root_heading: true

::: sharedbox.get_include
    options:
      show_root_heading: true
