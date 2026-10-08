---
icon: lucide/lightbulb
---

# How the module is built

The part of `sharedbox` that reads and writes shared memory is compiled
C++, in a module called `sharedbox._native`, and `update` and `snapshot`
are compiled too unless your class defines or inherits its own. Compiled
code has to be built for each Python version it runs on, which could mean a
long list of builds. This page explains how one build line gives a wheel
for every supported Python instead.

## One line, three wheels

The module is built with `nanobind`, a library that connects C++ to Python,
as C++20 against `sharedbox::headers`, the CMake target of
[`include/sharedbox/sharedbox.hpp`](https://github.com/jacopoabramo/sharedbox/blob/main/include/sharedbox/sharedbox.hpp):

```cmake
nanobind_add_module(_native
    STABLE_ABI
    FREE_THREADED
    LTO
    NB_DOMAIN sharedbox
    src/sharedbox/_native/codec.cpp
    src/sharedbox/_native/module.cpp
    src/sharedbox/_native/scalars.cpp
    src/sharedbox/_native/segment.cpp
    src/sharedbox/_native/types.cpp)
target_link_libraries(_native PRIVATE sharedbox::headers)
```

`STABLE_ABI` asks for a module that keeps working on later Python versions
without being rebuilt. `nanobind` can do that from Python
3.12,[^nanobind-cmake] so one wheel covers 3.12, 3.13, 3.14 and later.
`FREE_THREADED` asks for a module that runs without the global interpreter
lock (GIL), and only takes effect on the free-threaded Python that has no
GIL.[^nanobind-free-threaded] The trick is that `nanobind` quietly ignores
whichever option doesn't fit the Python doing the
build,[^nanobind-cmake][^nanobind-free-threaded] so the same line, run on
three Pythons, produces all three wheels:

| Wheel | Built on | Runs on |
| --- | --- | --- |
| `cp311-cp311` | CPython 3.11 | 3.11 only (the stable ABI needs 3.12) |
| `cp312-abi3` | CPython 3.12 | every normal CPython from 3.12 |
| `cp314-cp314t` | free-threaded CPython 3.14 | free-threaded 3.14 |

The free-threaded build needs its own wheel because it can't load
stable-ABI modules.[^nanobind-free-threaded] Python 3.15 starts a stable ABI
for free-threaded builds (`abi3t`), which could later fold that wheel into
the others.[^pep-803]

## The header in the wheel

The build also puts `include/sharedbox/` and the CMake config into the
wheel, so your own extensions can compile against the very header the
module was built with. [`get_include`][sharedbox.get_include] returns the
folder that holds it.

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
