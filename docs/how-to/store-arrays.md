---
icon: lucide/wrench
---

# How to store arrays

A box can hold arrays too, such as an image from a camera or a block of
samples. An array [field](../explanation/glossary.md#field) has a fixed shape and element type,
and it takes arrays from `numpy`, `torch` or any other library that can
hand its arrays over through DLPack or the buffer protocol, as long as they
are in CPU memory. This guide shows how to declare one, how to read and
write it, and the two ways to save a copy when the arrays are large.

## 1. Declare the field with a shape and a dtype

Put a [`Shape`][sharedbox.Shape] and a [`DType`][sharedbox.DType] in the
annotation. The type you annotate decides what a read gives you back: a
`numpy.ndarray`, a `torch.Tensor`, or, with
[`SupportsDLPack`][sharedbox.SupportsDLPack], an array object of
`sharedbox`'s own that any DLPack library can take:

```{.python}
--8<-- "docs/examples/store_arrays.py:declare"
```

## 2. Write and read an array

You assign and read an array field like any other:

```{.python}
--8<-- "docs/examples/store_arrays.py:write"
```

A write copies the array into the [box](../explanation/glossary.md#box), and a read copies it out
into a new array, so changing your array afterwards never changes the box,
and the other way round.

## 3. Read into an array you already have

A plain read gets fresh memory for a new array every time. If you read the
same field again and again, for example once per frame, pass an array you
already have to [`read_into`][sharedbox.SharedBox.read_into] instead. It
copies the field into that array and returns it:

```{.python}
--8<-- "docs/examples/store_arrays.py:read-into"
```

The array has to be writable, laid out in one block in C order
(C-contiguous), in CPU memory, and of the field's dtype and shape. On
Windows, getting fresh memory for a large array costs far more than the
copy itself, so reading into an array you keep is several times faster
there.

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

To read fields back as an array type other than `numpy`'s and `torch`'s,
call [`register_array_type`][sharedbox.register_array_type] with the type
and its `from_dlpack` function before you define the box class.

## 6. Handle the wrong shape or dtype

```{.python}
--8<-- "docs/examples/store_arrays.py:wrong-shape"
```

If you assign an array of the wrong shape, you get a `ValueError`; the
wrong dtype, or a value that isn't an array at all, raises `TypeError`.
Either way the field keeps its old value. For what a large array costs to
read and write, see
[How fast a box is](../explanation/performance.md#large-values).

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/store_arrays.py"
    ```
