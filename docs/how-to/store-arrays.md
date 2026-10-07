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

## 3. Read into an array you already have

A read allocates a new array every time. To read the same field again and
again, for example once per frame, pass an array you already have to
[`read_into`][sharedbox.SharedBox.read_into], which copies the field into
it and returns it:

```{.python}
--8<-- "docs/examples/store_arrays.py:read-into"
```

The array must be writable, C-contiguous, in CPU memory, and have the
field's dtype and shape. On Windows, getting fresh memory for a large array
costs more than copying into it, so reading into an array you keep is much
faster there.

## 4. Fill an array field in place

If your data comes from something that can write straight into memory you
hand it, such as a camera driver, [`writing`][sharedbox.SharedBox.writing]
saves you the extra copy. It gives you the field itself to fill:

```{.python}
--8<-- "docs/examples/store_arrays.py:writing"
```

The array starts out holding the field's current value. When the block
ends, the box counts the write and wakes everyone watching the field, even
if the block stopped halfway with an exception.

!!! warning "Everyone else waits while the block runs"
    The block holds the box's write lock, so every read and write of the
    box waits until it ends, in every process. A reader that waits longer
    than its lock timeout raises
    [`LockTimeoutError`][sharedbox.LockTimeoutError]. Do the slow work
    before the block, and keep the block itself short.

!!! warning "The array is yours only inside the block"
    Writing to the array after the block takes no lock and tells nobody,
    so a reader can see half of your change. Use it inside the `with`
    block and nowhere else.

## 5. Read with another library

A `SupportsDLPack` field reads back an object any DLPack library imports:

```{.python}
--8<-- "docs/examples/store_arrays.py:any-library"
```

For an array type other than numpy's and torch's, call
[`register_array_type`][sharedbox.register_array_type] with the type and
its `from_dlpack` function before defining the class.

## 6. Handle the wrong shape or dtype

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
