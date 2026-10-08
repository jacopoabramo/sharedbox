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
without being rebuilt, which `nanobind` can do from Python
3.12.[^nanobind-cmake] `FREE_THREADED` asks for a module that runs without
the global interpreter lock (GIL), on the free-threaded Python that has
none.[^nanobind-free-threaded] `nanobind` quietly ignores whichever option
doesn't fit the Python doing the
build,[^nanobind-cmake][^nanobind-free-threaded] so the same line, run on
three Pythons, gives three wheels:

```d2 title="One line, three wheels"
...@diagrams/style
direction: right
line: "nanobind_add_module(\nSTABLE_ABI FREE_THREADED)" {class: file}
p311: "CPython 3.11" {
  class: process
  tooltip: Too old for the stable ABI, so both options are ignored.
}
p312: "CPython 3.12" {
  class: process
  tooltip: Takes STABLE_ABI. FREE_THREADED does nothing on a Python with a GIL.
}
p314t: "free-threaded 3.14" {
  class: process
  tooltip: Takes FREE_THREADED. It can't load stable-ABI modules, so STABLE_ABI is ignored.
}
w311: "cp311-cp311\n3.11 only" {class: step}
w312: "cp312-abi3\n3.12 and later" {class: current}
w314t: "cp314-cp314t\nfree-threaded 3.14" {class: step}
line -> p311
line -> p312
line -> p314t
p311 -> w311
p312 -> w312
p314t -> w314t
```

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
