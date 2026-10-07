---
icon: lucide/wrench
---

# How to accept a box in a C++ extension

If you write a C++ extension, say a camera driver, your users can hand it a
[box](../explanation/glossary.md#box) directly, and your code reads and writes the same fields
their Python code does:

```python
with Frame() as frame:
    camera.run(frame)            # camera: an extension that supports sharedbox
```

Your extension reads and writes the box through `sharedbox/sharedbox.hpp`, a
header-only C++20 library that comes with the `sharedbox` wheel, so you
don't link any library from `sharedbox`. Your extension gets its own
[handle](../explanation/glossary.md#handle) on the [segment](../explanation/glossary.md#segment), which means it keeps
working even if the Python side closes or unlinks the box.

Writing C instead? See
[How to accept a box in a C extension](accept-a-box-in-c.md). For a program
that has no Python at all, see
[How to open a box from a program](open-a-box-from-a-program.md).

## Before you start

!!! note "What you need"

    A C++20 compiler on Windows or Linux, and sharedbox installed where
    your extension is built.

## Know the box you expect

Your code has to know which kind of box it accepts. The examples on these
pages use this class:

```python
from sharedbox import SharedBox


class Frame(SharedBox, identity="camera/frame/1"):
    exposure: float = 0.01
    count: int = 0
```

From it you work out two values once and keep them as constants:

- The [schema hash](../explanation/glossary.md#schema-hash): the first 8
  bytes of SHA-256 over `camera/frame/1|exposure:float:8|count:int:8`,
  read little-endian. For `Frame` it is `0x544efbe0815c923d`.
- The default box name, needed only to open the box by name: 16 hex digits
  of SHA-256 over the [identity](../explanation/glossary.md#identity)
  `camera/frame/1`. For `Frame` it is `d8dfe7af542f1d91`.

`sharedbox.hpp` doesn't compute SHA-256, so work both out with your own
library, or once in Python.
[The schema hash](../explanation/how-a-box-is-stored.md#the-schema-hash)
explains what the hashed text holds.

In C++ you name a [field](../explanation/glossary.md#field) by its position in the class, counting
from 0: `exposure` is field 0 and `count` is field 1.

## Declare the parameter

So that type checkers know your function takes a box, annotate the
parameter with [`SupportsSharedBox`][sharedbox.SupportsSharedBox] in your
extension's stubs:

```python
from sharedbox import SupportsSharedBox

def run(frame: SupportsSharedBox) -> None: ...
```

## Build against the header

Add `sharedbox` to `[build-system] requires` and build as C++20. Then point
your build at the header in one of two ways:

- Put [`sharedbox.get_include()`][sharedbox.get_include] on the include
  path.
- With CMake, add `Path(sharedbox.get_include()).parent` to
  `CMAKE_PREFIX_PATH`, call `find_package(sharedbox 0.4 CONFIG REQUIRED)`
  and link `sharedbox::headers` (C++) or `sharedbox::c` (C). The version
  is optional. Before 1.0 a request accepts only the same minor version,
  so `0.4` accepts 0.4.0 and later 0.4 releases but not 0.5.

`sharedbox::c` compiles `sharedbox_c.cpp` into the target that links it,
so your project has to enable the CXX language as well as C; if it doesn't,
configure stops with a message saying so. On Linux both targets link `rt` and
`Threads::Threads`, on Windows `bcrypt`.

!!! warning "The header trims `windows.h`"
    On Windows, `sharedbox.hpp` includes `windows.h` and `bcrypt.h`, with
    `windows.h` in its lean form and without the `min` and `max` macros:
    unless you defined them already, it defines `WIN32_LEAN_AND_MEAN` and
    `NOMINMAX` before the include and removes them afterwards. If your code
    needs the full `windows.h` or the `min` and `max` macros, include
    `windows.h` before `sharedbox.hpp`.

You write `sharedbox::handle`, but the header actually declares its C++
names in an inline namespace, `sharedbox::v2`. That namespace changes
whenever the C++ interface changes incompatibly, so libraries built against
different versions of the header can still be linked into one program. The
C functions `sbx_*` keep their names across versions. Names in
`sharedbox::detail` are internal and may change in any release.

!!! warning "Build shared libraries with hidden visibility on Linux"
    When two shared libraries built against different releases of the
    header are loaded into one program, the Linux dynamic linker uses one
    copy of each inline function and variable for both, even where the two
    differ. Build a shared library that uses `sharedbox::headers` or
    `sharedbox::c` with hidden visibility, so its copies stay private to it:

    ```cmake
    set_target_properties(reader PROPERTIES
        C_VISIBILITY_PRESET hidden
        CXX_VISIBILITY_PRESET hidden
        VISIBILITY_INLINES_HIDDEN ON)
    ```

`sharedbox_c.h` declares the `sbx_*` functions with hidden visibility on
GCC and Clang outside Windows, so each library keeps its own copy of them
whatever its target settings.

## Take the box from the capsule

With the build set up, taking the box is four steps:

1. Call `__sharedbox_box__` on the object you were given.
2. Take the handle out of the capsule with
   `sharedbox::handle::from_capsule`.
3. Rename the capsule `"used_sharedbox_box"`.
4. Compare the schema hash with the one you expect.

```cpp
#include <Python.h>
#include <sharedbox/sharedbox.hpp>

constexpr std::uint64_t FRAME_SCHEMA = 0x544efbe0815c923d;

static PyObject *run(PyObject *, PyObject *frame) {
    PyObject *capsule = PyObject_CallMethod(frame, "__sharedbox_box__", nullptr);
    if (capsule == nullptr)
        return nullptr;
    auto *given = static_cast<sbx_handle *>(PyCapsule_GetPointer(capsule, "sharedbox_box"));
    if (given == nullptr) {
        Py_DECREF(capsule);
        return nullptr;
    }
    sharedbox::result<sharedbox::handle> box = sharedbox::handle::from_capsule(given);
    if (box)
        PyCapsule_SetName(capsule, "used_sharedbox_box");
    Py_DECREF(capsule);
    if (!box)
        return PyErr_Format(PyExc_ValueError, "not a box this sharedbox.hpp can open");
    if (box->schema_hash() != FRAME_SCHEMA)
        return PyErr_Format(PyExc_TypeError, "expected a Frame box");
    // box is independent of the Python box from here on, and may move to another thread.
    std::int64_t count = 0;
    const auto read = box->read(1, std::as_writable_bytes(std::span(&count, 1)));
    if (!read)
        return PyErr_Format(PyExc_RuntimeError, "read failed: %d", static_cast<int>(read.error()));
    return PyLong_FromLongLong(count);
}
```

`from_capsule` checks the segment the handle points to, just as an attach
does. If the check passes, `box` takes over the capsule's handle and
releases it when `box` is destroyed. If it fails, the capsule keeps its
handle and releases it when the capsule is garbage collected, so nothing
leaks either way.

## Next steps

- [C and C++ interface](../reference/cpp-and-c-api.md): which calls may
  run on several threads, and how errors are returned
- [Segment layout](../reference/segment-layout.md): the layout and
  protocols the header implements
