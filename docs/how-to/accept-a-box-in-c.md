---
icon: lucide/wrench
---

# How to accept a box in a C extension

A C extension can take a [box](../explanation/glossary.md#box) from Python just as a C++ one
does, through the C interface `sharedbox/sharedbox_c.h`. Most of the work is
the same, so this guide only shows what differs from
[How to accept a box in a C++ extension](accept-a-box-in-cpp.md). It uses
the same `Frame` class and constants as
[that guide](accept-a-box-in-cpp.md#know-the-box-you-expect).

## Before you start

!!! note "What you need"

    A C compiler and a C++20 compiler on Windows or Linux. You need both
    because the C interface is itself written in C++;
    [Build against the header](accept-a-box-in-cpp.md#build-against-the-header)
    explains how it gets compiled into your extension.

## Build against the header

Follow [Build against the header](accept-a-box-in-cpp.md#build-against-the-header)
in the C++ guide, and with CMake link `sharedbox::c` instead of
`sharedbox::headers`.

## Take the box from the capsule

In C, `sbx_import` moves the capsule's [handle](../explanation/glossary.md#handle) into a
variable of your own, here `own`, and `sbx_release` lets go of it when
you're done:

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
        return PyErr_Format(PyExc_ValueError, "not a box sharedbox_c.h can open: %d", rc);
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

## What the C interface leaves out

`sharedbox_c.h` covers reading and writing, and little else. It opens or
imports a box (`sbx_open`, `sbx_import`), checks its schema hash
(`sbx_schema_hash`), reads and writes a field's bytes (`sbx_read`,
`sbx_write`) and releases the box (`sbx_release`). For fields of the
described types it also has `sbx_field_desc`, which gives you a field's kind
code and description, a typed `sbx_read_*` and `sbx_write_*` pair for
dates, times, datetimes, timedeltas, UUIDs, complex numbers, flags and the
position of an enum member or literal value, and `sbx_read_present`, which
tells you whether an optional field holds a value.

Waiting for changes, creating a box, `force_unlock` and `unlink` are only
in the C++ API, and `sharedbox_c.h` may be removed in a future major
version, so prefer `sharedbox.hpp` when you can use C++.

For a complete example, see
[`tests/cpp/consumer/`](https://github.com/jacopoabramo/sharedbox/blob/main/tests/cpp/consumer/),
a C library built this way against an installed wheel.

## Next steps

- [C and C++ interface](../reference/cpp-and-c-api.md): which calls may
  run on several threads, and how errors are returned
- [`sharedbox_c.h`](../reference/segment-layout.md#sharedbox_ch): what each
  function accepts and returns
