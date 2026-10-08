---
icon: lucide/wrench
---

# How to open a box from a program

A C++ or C program doesn't need Python to use a [box](../explanation/glossary.md#box): it can
open one by name, as long as some process created it. This guide reads,
writes and waits for changes in the `Frame` box of
[How to accept a box in a C++ extension](accept-a-box-in-cpp.md#know-the-box-you-expect),
whose default name is `d8dfe7af542f1d91`.

## Before you start

!!! note "What you need"

    A C++20 compiler and CMake 3.30 or newer, on Windows or Linux.

## Add sharedbox to the build

Without Python there's no wheel to take the header from, so add the
repository with CMake's `FetchContent` instead, and link
`sharedbox::headers`, or `sharedbox::c` for a C program:

```cmake
include(FetchContent)
FetchContent_Declare(sharedbox
    GIT_REPOSITORY https://github.com/jacopoabramo/sharedbox
    GIT_TAG vX.Y.Z)
FetchContent_MakeAvailable(sharedbox)
target_link_libraries(reader PRIVATE sharedbox::headers)    # or sharedbox::c
```

## Open the box and use it

Open the box by name and check its [schema hash](../explanation/glossary.md#schema-hash) to make
sure it's the box you expect. Then you can read, write or wait for changes.
This program prints `count` after each of the next three writes, then sets
it back to 0:

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

A C program does the same with `sbx_open`, `sbx_schema_hash`, `sbx_read`,
`sbx_write` and `sbx_release`, but it can't wait for changes, which
[the C interface leaves out](accept-a-box-in-c.md#what-the-c-interface-leaves-out).

## Create the box from C++

Your program can also be the one that creates the box. `handle::create`
makes a new box from C++, with the field table, record size, schema hash
and number of [waiter slots](../explanation/glossary.md#waiter-slot) you give it. Python code can
then attach to it, as long as its class has the same
[identity](../explanation/glossary.md#identity) and fields.

## Next steps

- [C and C++ interface](../reference/cpp-and-c-api.md): which calls may
  run on several threads, and how errors are returned
- [`sharedbox.hpp`](../reference/segment-layout.md#sharedboxhpp): every
  member of `sharedbox::handle`
