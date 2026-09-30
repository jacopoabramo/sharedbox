---
icon: lucide/lightbulb
---

# How the module is built

The part of sharedbox that reads and writes shared memory is a compiled
module, `sharedbox._native`. This page explains how one build line gives a
wheel for every supported Python.

## One line, three wheels

The module is built with nanobind, which connects C++ to Python, as C++20
against `sharedbox::headers`, the CMake target of
[`include/sharedbox/sharedbox.hpp`](https://github.com/jacopoabramo/sharedbox/blob/main/include/sharedbox/sharedbox.hpp):

```cmake
nanobind_add_module(_native
    STABLE_ABI
    FREE_THREADED
    LTO
    NB_DOMAIN sharedbox
    src/sharedbox/_native/codec.cpp
    src/sharedbox/_native/module.cpp
    src/sharedbox/_native/segment.cpp)
target_link_libraries(_native PRIVATE sharedbox::headers)
```

`STABLE_ABI` asks for a module that works on every later Python version
without recompiling. nanobind supports this from Python 3.12,[^nanobind-cmake]
so one wheel covers 3.12, 3.13, 3.14 and later. `FREE_THREADED` asks for a
module that runs without the GIL; it only takes effect on free-threaded
Python.[^nanobind-free-threaded] nanobind ignores whichever option does not
fit the interpreter doing the build,[^nanobind-cmake][^nanobind-free-threaded]
which is why one line produces all three wheels:

| Wheel | Built on | Runs on |
| --- | --- | --- |
| `cp311-cp311` | CPython 3.11 | 3.11 only (the stable ABI needs 3.12) |
| `cp312-abi3` | CPython 3.12 | every normal CPython from 3.12 |
| `cp314-cp314t` | free-threaded CPython 3.14 | free-threaded 3.14 |

Free-threaded Python cannot load stable-ABI modules.[^nanobind-free-threaded]
A stable ABI for free-threaded builds (`abi3t`) starts with Python
3.15.[^pep-803]

## The header in the wheel

The build also installs `include/sharedbox/` and the CMake config into the
wheel, so other extensions can compile against the same header.
[`get_include`][sharedbox.get_include] returns the folder that holds it.

## Sources

[^nanobind-cmake]:
    nanobind, CMake interface: `STABLE_ABI` (CPython 3.12 or newer),
    `NB_DOMAIN`.
    <https://nanobind.readthedocs.io/en/latest/api_cmake.html>

[^nanobind-free-threaded]:
    nanobind, "Free-threaded Python": `FREE_THREADED`, ignored on builds
    that do not support it; no stable ABI for free-threaded linked builds.
    <https://nanobind.readthedocs.io/en/latest/free_threaded.html>

[^pep-803]:
    PEP 803, the stable ABI for free-threaded builds (`abi3t`).
    <https://peps.python.org/pep-0803/>
