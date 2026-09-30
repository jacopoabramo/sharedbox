---
icon: lucide/file-code
---

# C and C++ interface

sharedbox installs two headers with its wheel, in the folder that
[`get_include`][sharedbox.get_include] returns:

| Header | What it declares | Specified in |
| --- | --- | --- |
| [`sharedbox/sharedbox.hpp`](https://github.com/jacopoabramo/sharedbox/blob/main/include/sharedbox/sharedbox.hpp) | the C++20 API: `sharedbox::handle`, `sharedbox::result`, `sharedbox::status` | [`sharedbox.hpp`](segment-layout.md#sharedboxhpp) |
| [`sharedbox/sharedbox_c.h`](https://github.com/jacopoabramo/sharedbox/blob/main/include/sharedbox/sharedbox_c.h) | the six `sbx_*` C functions and the status codes | [`sharedbox_c.h`](segment-layout.md#sharedbox_ch) |
| both | `sbx_handle`, the struct a capsule holds | [Capsule handle](segment-layout.md#capsule-handle) |

The Python side of the exchange, `__sharedbox_box__` and the capsule it
returns, is specified in
[The PyCapsule interface](segment-layout.md#the-pycapsule-interface). The
CMake targets are in [Build and packaging](segment-layout.md#build-and-packaging).

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
