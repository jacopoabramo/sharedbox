# capsule-consumer

This package is a small, complete C++ extension that takes a sharedbox box
and stream from Python and works with them on its own thread. Copy it when
you write a library whose C++ code needs data that Python code shares
through sharedbox.

```python
from sharedbox import SharedStream
from capsule_consumer import Producer, Sample, Settings

with (
    Settings(rate_hz=100.0, count=50) as settings,
    SharedStream.create(Sample, capacity=16) as stream,
):
    reader = stream.reader()
    with Producer(settings, stream):
        for _ in range(50):
            print(reader.receive(timeout=1.0))
Settings.unlink()
```

`Producer` starts a C++ thread that sends `Sample(i, i * 0.5)` items into
the stream. Before each send the thread reads `rate_hz` and `count` from
the `Settings` box, so a change that any process makes to the box takes
effect at the next item. The thread stops after `count` items, where 0
means no limit, or when `stop()` is called or the `with` block ends. It
then closes the stream, so readers receive the items still buffered and
then `EndOfStream`.

## What it shows

The extension takes the box and the stream with `take_box` and
`take_stream`, the two functions of the `sharedbox/python.hpp` header.
Each calls the Python object's `__sharedbox_box__` or
`__sharedbox_stream__` method, which returns a capsule (a Python object
wrapping a C pointer) describing the shared-memory segment, and opens a
handle of its own on that segment. It then compares the segment's schema
hash, a 64-bit number computed from the box class's identity and fields or
from the stream's item type, with the one the caller passes. The handles
do not depend on the Python objects, so the thread keeps working with them
without the GIL and without calling into Python.

The extension never writes a schema hash into its source. The package's
`__init__.py` computes them from its own classes with
`sharedbox.get_schema_hash` and passes them to the extension at import, so
when `Settings` or `Sample` changes, the hash the extension expects changes
with it.

The hash proves that a box is a `Settings`, but not that the C++ code reads
it as `Settings` declares it. The constructor therefore also checks the
kind code the box stores for each field, `sharedbox::kind_float` for field
0, `rate_hz`, and `sharedbox::kind_int` for field 1, `count`, and that a
stream item is a 16-byte record of an `int64` index at offset 0 and a
`double` value at offset 8, the members `Sample` declares. A change to `Settings` or `Sample` needs the
same change in `_producer.cpp`: the `Item` struct for a field of `Sample`,
and the list of expected fields and the reads in the thread for a field of
`Settings`. Until it is made, these checks refuse the classes.

`Producer` guards its thread with a mutex, so two Python threads that call
`start()` and `stop()` at once on a free-threaded build cannot both start a
thread. `stop()` interrupts a send that waits for a slow reader, releases
the GIL while it joins the thread, and raises the error that ended the
thread, if one did, as `RuntimeError`.

## Build it

```sh
pip install ./examples/capsule-consumer
```

The build needs scikit-build-core, nanobind and sharedbox, which
`pyproject.toml` lists, CMake 3.30.3 or newer and a C++20 compiler.
`CMakeLists.txt` finds sharedbox with `find_package(sharedbox CONFIG
REQUIRED)` and nothing more, because scikit-build-core searches the build
environment's site-packages, where the sharedbox wheel keeps its CMake
files.

The project builds the same three kinds of wheel that sharedbox does:

- `cp311-cp311`, for CPython 3.11 only;
- `cp312-abi3`, one wheel for CPython 3.12 and newer, built against the
  Limited API, the part of the C API that stays the same across Python
  versions, which `python.hpp` keeps to;
- `cp314-cp314t`, for free-threaded CPython 3.14.

One `nanobind_add_module(_producer STABLE_ABI FREE_THREADED ...)` line
covers all three: nanobind uses the Limited API from Python 3.12 on and
leaves it out on a free-threaded interpreter, which has none.
`wheel.py-api = "cp312"` in `pyproject.toml` makes scikit-build-core tag the
3.12 and newer builds `abi3`.

CMake finds only a Python built with the GIL unless told otherwise, so
`CMakeLists.txt` sets `Python_FIND_ABI` to `ANY;ANY;ANY;ANY`, which also
accepts a free-threaded one. On Windows that build also needs
`Py_GIL_DISABLED` defined, which the Windows `pyconfig.h` leaves out, and
`Python::Module` adds it from CMake 3.30.3 on, which is why the project
requires that version.

## Run the tests

```sh
pip install pytest
pytest examples/capsule-consumer/tests
```
