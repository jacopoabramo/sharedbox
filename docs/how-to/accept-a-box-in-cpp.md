---
icon: lucide/wrench
---

# How to accept a box in a C++ extension

A Python user hands your extension the [box](../explanation/glossary.md#box)
object itself:

```python
with Frame() as frame:
    camera.run(frame)            # camera: an extension that supports sharedbox
```

Your extension then reads and writes the box through the header-only C++20
library `sharedbox/sharedbox.hpp`, which sharedbox installs with its wheel.
Your extension links no library from sharedbox. The extension gets its own
[handle](../explanation/glossary.md#handle) on the
[segment](../explanation/glossary.md#segment), so closing or unlinking the
box in Python does not affect it.

For C code, see [How to accept a box in a C extension](accept-a-box-in-c.md).
For a program with no Python, see
[How to open a box from a program](open-a-box-from-a-program.md).

## Before you start

!!! note "What you need"

    A C++20 compiler on Windows or Linux, and sharedbox installed where
    your extension is built.

## Know the box you expect

The examples on these pages use this class:

```python
from sharedbox import SharedBox


class Frame(SharedBox, identity="camera/frame/1"):
    exposure: float = 0.01
    count: int = 0
```

Your code needs two values computed from the class and kept as
constants:

- The [schema hash](../explanation/glossary.md#schema-hash): the first 8
  bytes of SHA-256 over `camera/frame/1|exposure:float:8|count:int:8`,
  read little-endian. For `Frame` it is `0x544efbe0815c923d`.
- The default box name, needed only to open the box by name: 16 hex digits
  of SHA-256 over the [identity](../explanation/glossary.md#identity)
  `camera/frame/1`. For `Frame` it is `d8dfe7af542f1d91`.

`sharedbox.hpp` does not compute SHA-256, so compute both with your own
library, or once in Python. [The schema hash](../explanation/how-a-box-is-stored.md#the-schema-hash)
explains what the hashed text holds.

A [field](../explanation/glossary.md#field) is addressed by its position
in declaration order: `exposure` is field 0, `count` is field 1.

## Declare the parameter

In the stubs of your extension, annotate the box parameter with
[`SupportsSharedBox`][sharedbox.SupportsSharedBox]:

```python
from sharedbox import SupportsSharedBox

def run(frame: SupportsSharedBox) -> None: ...
```

## Build against the header

Add `sharedbox` to `[build-system] requires` and build as C++20. Then do
one of the following:

- Put [`sharedbox.get_include()`][sharedbox.get_include] on the include
  path.
- With CMake, add `Path(sharedbox.get_include()).parent` to
  `CMAKE_PREFIX_PATH`, call `find_package(sharedbox 0.3 CONFIG REQUIRED)`
  and link `sharedbox::headers` (C++) or `sharedbox::c` (C). The version
  is optional. Before 1.0 a request accepts only the same minor version,
  so `0.3` accepts 0.3.0 and later 0.3 releases but not 0.4.

`sharedbox::c` compiles `sharedbox_c.cpp` into the target that links it,
so the project must enable the CXX language as well as C; configure stops
with a message if it does not. On Linux both targets link `rt` and
`Threads::Threads`, on Windows `bcrypt`.

On Windows `sharedbox.hpp` includes `windows.h` and `bcrypt.h`. It
includes `windows.h` in its lean form and without the `min` and `max`
macros: unless you defined them already, it defines `WIN32_LEAN_AND_MEAN`
and `NOMINMAX` before that include and removes them at its end. Code that
needs the full `windows.h` or the `min` and `max` macros must include
`windows.h` before `sharedbox.hpp`.

The header declares its C++ names in `sharedbox::v2`, an inline
namespace, so code still writes `sharedbox::handle`. The inline namespace
changes when the header's C++ interface changes incompatibly, so libraries
built against headers with different inline namespaces can be linked into
one program. The C functions `sbx_*` keep their names across versions.
Names in `sharedbox::detail` are internal and may change in any release
without a new inline namespace.

On Linux, when two shared libraries built against different releases of
the header are loaded into one program, the dynamic linker uses one copy of
each inline function and variable for both, even where the two copies
differ. A shared library that uses `sharedbox::headers` or `sharedbox::c`
should build with hidden visibility, so its copies stay private to it:

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
        return PyErr_Format(PyExc_ValueError, "not a sharedbox layout 1.x box");
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

`from_capsule` checks the segment the handle points to, as an attach does.
On success it takes over the capsule's handle, and destroying `box`
releases it. When it fails, the capsule keeps its handle and releases it
when the capsule is garbage collected.

## Next steps

- [C and C++ interface](../reference/cpp-and-c-api.md): which calls may
  run on several threads, and how errors are returned
- [Segment layout](../reference/segment-layout.md): the layout and
  protocols the header implements
