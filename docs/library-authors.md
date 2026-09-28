# Accepting a box in a C++ or C library

This page is for authors of C++ or C code that reads and writes a sharedbox
box: an extension module that takes a box from Python, or a standalone
program with no Python. Both use the header-only C++20 library
`sharedbox/sharedbox.hpp` that sharedbox installs with its wheel, or its
minimal C interface `sharedbox/sharedbox_c.h`. Nothing is linked from
sharedbox itself. The layout and protocols the header implements are in
[design/segment-layout.md](design/segment-layout.md).

The examples use this class:

```python
from sharedbox import SharedBox


class Frame(SharedBox, identity="camera/frame/1"):
    exposure: float = 0.01
    count: int = 0
```

Its schema hash is the first 8 bytes of SHA-256 over
`camera/frame/1|exposure:float:8|count:int:8`, read little-endian:
`0x544efbe0815c923d`. Its default box name is 16 hex digits of SHA-256
over `camera/frame/1`: `d8dfe7af542f1d91`. `sharedbox.hpp` carries no
SHA-256, so compute both with your own library, or once in Python, and
write them into your code. A field is addressed by its position in
declaration order: `exposure` is field 0, `count` field 1.

## An extension that accepts a box

Python users pass the box object to your function. Declare it in your
stubs with `SupportsSharedBox`:

```python
from sharedbox import SupportsSharedBox

def run(frame: SupportsSharedBox) -> None: ...
```

### Build

Add `sharedbox` to `[build-system] requires` and build as C++20. Then
either:

- put `sharedbox.get_include()` on the include path; or
- with CMake, add `Path(sharedbox.get_include()).parent` to
  `CMAKE_PREFIX_PATH`, call `find_package(sharedbox CONFIG REQUIRED)` and
  link `sharedbox::headers` (C++) or `sharedbox::c` (C).

`sharedbox::c` compiles `sharedbox_c.cpp` into the target that links it,
so the project must enable the CXX language as well as C; configure stops
with a message if it does not. On Linux both targets link `rt` and
`Threads::Threads`, on Windows `bcrypt`.

On Windows `sharedbox.hpp` includes `windows.h` and `bcrypt.h`. Unless you
defined them already, it defines `NOMINMAX` and `WIN32_LEAN_AND_MEAN`
before that include and removes them at its end.

The header declares its C++ names in `sharedbox::v1`, an inline
namespace, so code still writes `sharedbox::handle`. The inline namespace
changes when the header's C++ interface changes incompatibly, so two
libraries built against different versions of the header can live in one
program. The C functions `sbx_*` keep their names across versions. Names
in `sharedbox::detail` are internal and may change in any release.

### C++

Call `__sharedbox_box__`, take the handle out of the capsule with
`sharedbox::handle::from_capsule`, rename the capsule
`"used_sharedbox_box"`, and compare the schema hash with the one you
expect:

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

`from_capsule` checks the segment the handle points to as an attach does,
and on success takes over the capsule's handle: destroying `box` releases
it. When it fails, the capsule keeps its handle and releases it when the
capsule is garbage collected. The handle has its own mapping, so it stays
valid after the Python box is closed or unlinked.

### C

The same with `sharedbox_c.h`. `sbx_import` takes the capsule's handle
into `own`, and `sbx_release` releases it:

```c
#include <Python.h>
#include <sharedbox/sharedbox_c.h>

#define FRAME_SCHEMA UINT64_C(0x544efbe0815c923d)

static PyObject *run(PyObject *self, PyObject *frame) {
    PyObject *capsule = PyObject_CallMethod(frame, "__sharedbox_box__", NULL);
    if (capsule == NULL)
        return NULL;
    sbx_handle *given = PyCapsule_GetPointer(capsule, "sharedbox_box");
    if (given == NULL) {
        Py_DECREF(capsule);
        return NULL;
    }
    sbx_handle own;
    int rc = sbx_import(given, &own);
    if (rc == SBX_OK)
        PyCapsule_SetName(capsule, "used_sharedbox_box");
    Py_DECREF(capsule);
    if (rc != SBX_OK)
        return PyErr_Format(PyExc_ValueError, "not a sharedbox layout 1.x box: %d", rc);
    if (sbx_schema_hash(&own) != FRAME_SCHEMA) {
        sbx_release(&own);
        return PyErr_Format(PyExc_TypeError, "expected a Frame box");
    }
    int64_t count = 0;
    rc = sbx_read(&own, 1, &count, sizeof count, NULL, NULL);
    sbx_release(&own);
    if (rc != SBX_OK)
        return PyErr_Format(PyExc_RuntimeError, "read failed: %d", rc);
    return PyLong_FromLongLong(count);
}
```

`sharedbox_c.h` is minimal and may be removed in a future major version.
It has six functions: `sbx_open`, `sbx_import`, `sbx_read`, `sbx_write`,
`sbx_schema_hash` and `sbx_release`. Waiting for changes, creating a box,
`force_unlock` and `unlink` are in the C++ API only.

## A standalone program

A program with no Python opens a box by name. Add the repository with
CMake's `FetchContent` (CMake 3.30 or newer):

```cmake
include(FetchContent)
FetchContent_Declare(sharedbox
    GIT_REPOSITORY https://github.com/jacopoabramo/sharedbox
    GIT_TAG vX.Y.Z)
FetchContent_MakeAvailable(sharedbox)
target_link_libraries(reader PRIVATE sharedbox::headers)    # or sharedbox::c
```

Then open the box, check its schema hash, and read, write or wait for
changes:

```cpp
#include <sharedbox/sharedbox.hpp>

#include <cstdio>

constexpr std::uint64_t FRAME_SCHEMA = 0x544efbe0815c923d;

int main() {
    auto box = sharedbox::handle::open("d8dfe7af542f1d91", sharedbox::seconds(1.0));
    if (!box || box->schema_hash() != FRAME_SCHEMA)
        return 1;
    auto slot = box->register_waiter();
    if (!slot)
        return 1;
    std::uint64_t seen = box->generation();
    for (int i = 0; i < 3; ++i) {
        auto woken = box->wait(*slot, seen, sharedbox::seconds(10.0));
        if (!woken)
            return 1;                     // sharedbox::status::timeout after 10 s
        seen = box->generation();
        std::int64_t count = 0;
        if (!box->read(1, std::as_writable_bytes(std::span(&count, 1))))
            return 1;
        std::printf("count %lld\n", static_cast<long long>(count));
    }
    const std::int64_t reset = 0;
    const sharedbox::value values[] = {{1, std::as_bytes(std::span(&reset, 1))}};
    if (!box->write(values, sharedbox::seconds(1.0)))
        return 1;
    return 0;                             // the destructor releases the slot and unmaps
}
```

In C: `sbx_open`, `sbx_schema_hash`, `sbx_read`, `sbx_write` and
`sbx_release`.

`handle::create` makes a new box from C++, with the field table, record
size, schema hash and waiter slot count the caller gives; Python attaches
to it when its class has the same identity and fields.

## Thread safety

Every member function of `sharedbox::handle` may run on several threads at
once with one handle. Destroying or moving a handle must not overlap
another call on it. The same holds for an `sbx_handle` and the C
functions.

## Errors

Every call that can fail returns `sharedbox::result<T>`: a value, or a
`sharedbox::status` from `error()`. On C++20 `result` is the header's own
type with the interface of `std::expected<T, status>` (`has_value`,
`operator bool`, `operator*`, `->`, `value`, `error`, `and_then`,
`transform`, `or_else`, `value_or`); on C++23 with `std::expected`, it is
`std::expected<T, status>`. Build every translation unit of a program as
C++20 or every one as C++23, so they all see the same `result`. Nothing
throws, so the header builds with `-fno-exceptions`. The C functions
return the same codes as `int` (`SBX_OK`, `SBX_E_*`).

`status::os` means an OS call failed; `errno` (Linux) or `GetLastError()`
(Windows) still holds its code when the call returns.
