---
icon: lucide/wrench
---

# How to accept a stream in a C++ extension

If you write a C++ extension, say a data logger, your users can hand it a
[stream](../explanation/glossary.md#stream) directly, and your code sends or
receives the same items their Python code does:

```python
with SharedStream.create(float, capacity=64) as readings:
    logger.record(readings)  # logger: an extension that supports sharedbox
```

Your extension uses the stream through `sharedbox/sharedbox.hpp`, the
header-only C++20 library that comes with the `sharedbox` wheel. It gets its
own [handle](../explanation/glossary.md#handle) on the
[segment](../explanation/glossary.md#segment), so it keeps working even if
the Python side closes or unlinks the stream. The handle claims no sender
and opens no reader: your code does that itself, as a second process
would.

## Before you start

!!! note "What you need"

    A C++20 compiler on Windows or Linux, and sharedbox installed where
    your extension is built.

## Know the stream you expect

Your code has to know which item type it accepts, and it checks that
through the stream's [schema hash](../explanation/glossary.md#schema-hash):
the first 8 bytes of SHA-256 over `stream|` followed by the item type's
text, read little-endian. For a stream of `float` the text is `float:8`, so
the hash is `0xca61a9c40c375b90`. `sharedbox.hpp` doesn't compute SHA-256,
so work the hash out once, for example in Python, and paste it into your
code:

```python
import hashlib

digest = hashlib.sha256(b"stream|float:8").digest()
print(hex(int.from_bytes(digest[:8], "little")))  # 0xca61a9c40c375b90
```

[The schema hash](../explanation/how-a-box-is-stored.md#the-schema-hash)
explains how to write the type text of other item types.

## Declare the parameter

So that type checkers know your function takes a stream, annotate the
parameter with [`SupportsSharedStream`][sharedbox.SupportsSharedStream] in
your extension's stubs:

```python
from sharedbox import SupportsSharedStream


def record(readings: SupportsSharedStream) -> None: ...
```

## Build against the header

A stream needs the same build as a box: add `sharedbox` to
`[build-system] requires`, build as C++20 and link `sharedbox::headers`.
[Build against the header](accept-a-box-in-cpp.md#build-against-the-header)
gives the details, including the visibility settings a shared library
needs on Linux.

## Take the stream from the capsule

With the build set up, taking the stream is four steps:

1. Call `__sharedbox_stream__` on the object you were given.
2. Take the handle out of the capsule with
   `sharedbox::stream::from_capsule`.
3. Rename the capsule `"used_sharedbox_stream"`.
4. Compare the schema hash with the one you expect.

```cpp
#include <Python.h>
#include <sharedbox/sharedbox.hpp>

constexpr std::uint64_t READING_SCHEMA = 0xca61a9c40c375b90;

static PyObject *record(PyObject *, PyObject *readings) {
    PyObject *capsule = PyObject_CallMethod(readings, "__sharedbox_stream__", nullptr);
    if (capsule == nullptr)
        return nullptr;
    auto *given = static_cast<sbx_handle *>(PyCapsule_GetPointer(capsule, "sharedbox_stream"));
    if (given == nullptr) {
        Py_DECREF(capsule);
        return nullptr;
    }
    sharedbox::result<sharedbox::stream> stream = sharedbox::stream::from_capsule(given);
    if (stream)
        PyCapsule_SetName(capsule, "used_sharedbox_stream");
    Py_DECREF(capsule);
    if (!stream)
        return PyErr_Format(PyExc_ValueError, "not a stream this sharedbox.hpp can open");
    if (stream->schema_hash() != READING_SCHEMA)
        return PyErr_Format(PyExc_TypeError, "expected a stream of float");
    // stream is independent of the Python stream from here on, and may move to another thread.
    auto reader = stream->reader(sharedbox::read_mode::lossless, sharedbox::start_at::oldest);
    if (!reader)
        return PyErr_Format(PyExc_RuntimeError, "no reader: %d", static_cast<int>(reader.error().code));
    double reading = 0.0;
    const auto got = reader->receive(std::as_writable_bytes(std::span(&reading, 1)), sharedbox::seconds(1.0));
    if (!got)
        return PyErr_Format(PyExc_RuntimeError, "receive failed: %d", static_cast<int>(got.error().code));
    return PyFloat_FromDouble(reading);
}
```

`from_capsule` checks the segment the handle points to, just as an attach
does, including its kind, so a box's handle fails with
`status::kind_mismatch`. If the check passes, `stream` takes over the
capsule's handle and releases it when `stream` is destroyed; if it fails,
the capsule keeps its handle and releases it when it is garbage collected,
so nothing leaks either way. A reader or a sender made from `stream` must
not outlive it.

To send instead, claim the sender with `stream->sender()`. It returns
`status::busy` while another end holds the sender, the Python side
included, and destroying the sender ends the stream for its readers.

## Next steps

- [How to send items through a stream](send-items-through-a-stream.md):
  modes, readers and the sender as Python sees them
- [C and C++ interface](../reference/cpp-and-c-api.md): which calls may
  run on several threads, and how errors are returned
- [Segment layout](../reference/segment-layout.md): the layout and
  protocols the header implements
