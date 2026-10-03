---
icon: lucide/wrench
---

# How to store arrays

An array [field](../explanation/glossary.md#field) holds an array of a
fixed shape and element type, from any library that exports its arrays
through DLPack or the buffer protocol, in CPU memory.

## 1. Declare the field with a shape and a dtype

[`Shape`][sharedbox.Shape] and [`DType`][sharedbox.DType] go in the
annotation. The annotated type decides what a read returns: a
`numpy.ndarray`, a `torch.Tensor`, or with
[`SupportsDLPack`][sharedbox.SupportsDLPack] sharedbox's own array object:

```{.python}
--8<-- "docs/examples/store_arrays.py:declare"
```

## 2. Write and read an array

```{.python}
--8<-- "docs/examples/store_arrays.py:write"
```

A write copies the array into the [box](../explanation/glossary.md#box),
and a read copies it out into a new array, so the two never share memory.

## 3. Read with another library

A `SupportsDLPack` field reads back an object any DLPack library imports:

```{.python}
--8<-- "docs/examples/store_arrays.py:any-library"
```

For an array type other than numpy's and torch's, call
[`register_array_type`][sharedbox.register_array_type] with the type and
its `from_dlpack` function before defining the class.

## 4. Handle the wrong shape or dtype

```{.python}
--8<-- "docs/examples/store_arrays.py:wrong-shape"
```

The wrong dtype, or a value that is not an array, raises `TypeError`.

A large array costs a copy on every read and write; see
[How fast a box is](../explanation/performance.md#large-values).

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/store_arrays.py"
    ```
