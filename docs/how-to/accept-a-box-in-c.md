---
icon: lucide/wrench
---

# How to accept a box in a C extension

A C extension takes a [box](../explanation/glossary.md#box) from Python
the same way a C++ one does, through the minimal C interface
`sharedbox/sharedbox_c.h`. This guide shows only what differs from
[How to accept a box in a C++ extension](accept-a-box-in-cpp.md), and uses
the same `Frame` class and constants as
[that guide](accept-a-box-in-cpp.md#know-the-box-you-expect).

## Before you start

!!! note "What you need"

    A C compiler and a C++20 compiler on Windows or Linux.
    [Build against the header](accept-a-box-in-cpp.md#build-against-the-header)
    says why C code needs the C++ compiler too.

## Build against the header

Follow [Build against the header](accept-a-box-in-cpp.md#build-against-the-header)
in the C++ guide, and with CMake link `sharedbox::c`.

## Take the box from the capsule

`sbx_import` takes the capsule's
[handle](../explanation/glossary.md#handle) into `own`, and `sbx_release`
releases it:

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

## What the C interface leaves out

`sharedbox_c.h` is minimal and may be removed in a future major version.
It has six functions: `sbx_open`, `sbx_import`, `sbx_read`, `sbx_write`,
`sbx_schema_hash` and `sbx_release`. Waiting for changes, creating a box,
`force_unlock` and `unlink` are in the C++ API only.

[`tests/cpp/consumer/`](https://github.com/jacopoabramo/sharedbox/blob/main/tests/cpp/consumer/)
is a C library built this way against an installed wheel.

## Next steps

- [C and C++ interface](../reference/cpp-and-c-api.md): which calls may
  run on several threads, and how errors are returned
- [`sharedbox_c.h`](../reference/segment-layout.md#sharedbox_ch): what each
  function accepts and returns
