---
icon: lucide/code
---

# Arrays

| Name | What it does |
| --- | --- |
| [`Shape`][sharedbox.Shape] | the dimensions of an array field; a numpy field with a default array takes them from it |
| [`DType`][sharedbox.DType] | the element type of an array field; a numpy annotation names it itself |
| [`SupportsDLPack`][sharedbox.SupportsDLPack] | an object a DLPack library can import; a field annotated with it reads back sharedbox's own array |
| [`register_array_type`][sharedbox.register_array_type] | makes fields of another array type read back through that library's `from_dlpack` |

::: sharedbox.Shape
    options:
      show_root_heading: true

::: sharedbox.DType
    options:
      show_root_heading: true

::: sharedbox.SupportsDLPack
    options:
      show_root_heading: true

::: sharedbox.register_array_type
    options:
      show_root_heading: true
