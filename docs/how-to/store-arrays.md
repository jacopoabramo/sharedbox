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

With `numpy`, annotate the field as
`numpy.ndarray[tuple[int, int], numpy.dtype[numpy.uint8]]`: one `int` for
each dimension, then the dtype. The sizes come from the array you give as
the field's default:

```{.python}
--8<-- "docs/examples/store_arrays.py:declare"
```

The `frame` field holds 4 by 6 values of `uint8`, and a read gives back a
`numpy.ndarray`. Only a plain default gives the sizes (`= array` or
`field(default=array)`), not a `default_factory`.

Two things can go in the annotation as well, as `raw` shows:

- A [`Shape`][sharedbox.Shape] gives the sizes. A field needs one when it
  has no default array. An array inside a record or a stream item always
  needs one, because its sizes are never taken from a default.
- A [`DType`][sharedbox.DType] gives the element type. A field needs one
  when its annotation names none: a plain `numpy.ndarray`, a
  `torch.Tensor`, or [`SupportsDLPack`][sharedbox.SupportsDLPack], an array
  object of `sharedbox`'s own that any DLPack library can take. `bfloat16`
  also needs a `DType`.

When the annotation gives the number of dimensions, the `Shape` or the
default must have as many: a `Shape(4, 6, 3)` or a 3-dimensional default
under `tuple[int, int]` raises a `TypeError` when the class is defined.

## 2. Write and read an array

You assign and read an array field like any other:

```{.python}
--8<-- "docs/examples/store_arrays.py:write"
```

A write copies the array into the [box](../explanation/glossary.md#box), and a read copies it out
into a new array, so changing your array afterwards never changes the box,
and the other way round. For a large array, those copies and the new
memory are what you pay for; switch between the three ways to see where
each one goes:

```d2 title="Where the bytes go"
...@diagrams/style
label: "A write copies your array into the field. A plain read copies the field into a new array, which needs fresh memory every time."
grid-columns: 1
vertical-gap: 30
pic: "" {
  style.stroke-width: 0
  style.fill: transparent
  grid-columns: 3
  horizontal-gap: 160
  yours: "your array" {class: step}
  field: "field in the box" {class: hardware}
  fresh: "new array" {class: step}
  yours -> field: "write: one copy"
  field -> fresh: "read: one copy"
  field -> yours: "read_into: one copy" {style.opacity: 0}
}
code: "" {
  style.stroke-width: 0
  style.fill: transparent
  code: |python
    1 -> sensor.frame = np.full((4, 6), 7, np.uint8)
    2 -> frame = sensor.frame
    3    sensor.read_into("frame", frame)
    4    with sensor.writing("frame") as frame:
    5        frame[0, :] = 255
  |
}
scenarios: {
  read_into: {
    label: "read_into copies the field into an array you already have, so no new memory is needed."
    pic.fresh.style.opacity: 0.3
    (pic.field -> pic.fresh)[0].style.opacity: 0.3
    (pic.yours -> pic.field)[0].style.opacity: 0
    (pic.field -> pic.yours)[0].style.opacity: 1
    (pic.field -> pic.yours)[0].style.stroke-width: 4
    code.code: |python
      1    sensor.frame = np.full((4, 6), 7, np.uint8)
      2    frame = sensor.frame
      3 -> sensor.read_into("frame", frame)
      4    with sensor.writing("frame") as frame:
      5        frame[0, :] = 255
    |
  }
  writing: {
    label: "writing hands you the field itself. Your code changes the bytes where they live, so nothing is copied."
    pic.yours.style.opacity: 0.3
    pic.fresh.style.opacity: 0.3
    (pic.field -> pic.fresh)[0].style.opacity: 0.3
    (pic.yours -> pic.field)[0].style.opacity: 0.3
    pic.field.label: "field in the box,\nfilled in place"
    pic.field.style.stroke-width: 4
    code.code: |python
      1    sensor.frame = np.full((4, 6), 7, np.uint8)
      2    frame = sensor.frame
      3    sensor.read_into("frame", frame)
      4 -> with sensor.writing("frame") as frame:
      5 ->     frame[0, :] = 255
    |
  }
}
```

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
ends, the box counts the write and wakes everyone watching the field. That
happens even if the block stops halfway with an exception, and then the
field keeps whatever the block wrote before it stopped.

!!! warning "Everyone else waits while the block runs"
    The block holds the box's write lock, so every read and write of the
    box waits until it ends, in every process. A reader that waits longer
    than its lock timeout, 5 s unless the class sets `lock_timeout`, raises
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
