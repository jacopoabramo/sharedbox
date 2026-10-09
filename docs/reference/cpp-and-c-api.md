---
icon: lucide/file-code
---

# C and C++ interface

C and C++ code reaches a box through two headers that come with the
`sharedbox` wheel, in the folder that [`get_include`][sharedbox.get_include]
returns. This page says where each one is specified; to get started, follow
[How to accept a box in a C++ extension](../how-to/accept-a-box-in-cpp.md)
instead.

| Header | What it declares | Specified in |
| --- | --- | --- |
| [`sharedbox/sharedbox.hpp`](https://github.com/jacopoabramo/sharedbox/blob/main/include/sharedbox/sharedbox.hpp), which includes `core.hpp` and `box.hpp` | the C++20 API: `sharedbox::handle`, `sharedbox::result`, `sharedbox::error`, `sharedbox::status`, `sharedbox::type_view` and the `encode_*` and `decode_*` functions of the value types; the inline namespace is `v3` | [`sharedbox.hpp`](segment-layout.md#sharedboxhpp) |
| [`sharedbox/sharedbox_c.h`](https://github.com/jacopoabramo/sharedbox/blob/main/include/sharedbox/sharedbox_c.h) | the `sbx_*` C functions, including `sbx_field_desc` and the typed reads and writes, and the status codes | [`sharedbox_c.h`](segment-layout.md#sharedbox_ch) |
| both | `sbx_handle`, the struct a capsule holds | [Capsule handle](segment-layout.md#capsule-handle) |

The Python side of the exchange, `__sharedbox_box__` and the capsule it
returns, is specified in
[The PyCapsule interface](segment-layout.md#the-pycapsule-interface). The
CMake targets are in [Build and packaging](segment-layout.md#build-and-packaging).

## Thread safety

Which calls you may make from several threads on one
[handle](../explanation/glossary.md#handle) is the same whether you use
`sharedbox::handle` or an `sbx_handle` with the C functions. The rules are
listed in [`sharedbox.hpp`](segment-layout.md#sharedboxhpp).

## Errors

- [Implementation language](segment-layout.md#implementation-language):
  `sharedbox::result<T>`, which every C++ call that can fail returns, its
  `sharedbox::error`, and `to_expected` on C++23
- [`sharedbox.hpp`](segment-layout.md#sharedboxhpp): what each
  `sharedbox::status` means, including `status::os`, `status::kind_mismatch`
  (a segment of another kind) and `status::foreign` (a magic this version
  does not know)
- [`sharedbox_c.h`](segment-layout.md#sharedbox_ch): the `SBX_OK` and
  `SBX_E_*` codes the C functions return, including `SBX_E_KIND` (-11) and
  `SBX_E_FOREIGN` (-12)
