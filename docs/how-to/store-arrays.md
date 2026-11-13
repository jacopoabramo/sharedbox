---
icon: lucide/wrench
---

# How to store arrays

A box can hold arrays too, such as the scan of an envelope or a block of
samples. An array [field](../explanation/glossary.md#field) has an element
type and a shape, fixed or up to a maximum, and it takes arrays from
`numpy`, `torch` or any other library that can hand its arrays over
through DLPack or the buffer protocol, as long as they are in CPU memory.
This guide shows how to declare one, how to read and write it, the two
ways to save a copy when the arrays are large, and how to store an array
whose shape changes.

## 1. Declare the field with a shape and a dtype

With `numpy`, annotate the field with `numpy.typing.NDArray` and its dtype,
and give its sizes with a [`Shape`][sharedbox.Shape] beside it in
`Annotated`:

```{.python}
--8<-- "docs/examples/store_arrays.py:declare"
```

The `scan` field holds 4 by 6 values of `uint8` and the `gain` field holds
2 values, because their `Shape` says so. A read gives back a
`numpy.ndarray`. The `Shape` is the only place the sizes come from: a
default array gives the field its first value and must have that shape, or
a `TypeError` names the two that disagree when the class is defined.

A [`DType`][sharedbox.DType] goes in the annotation as well, as `raw`
shows, and gives the element type. A field needs one when its annotation
names none: a plain `numpy.ndarray`, a `torch.Tensor`, or
[`SupportsDLPack`][sharedbox.SupportsDLPack], an array object of
`sharedbox`'s own that any DLPack library can take. `bfloat16` also needs a
`DType`.

An array inside a record is declared the same way, with the `Shape` in the
member's annotation:

```{.python}
--8<-- "docs/examples/store_arrays.py:record"
```

That holds for a dataclass, an `attrs` class, a `NamedTuple` and a
`msgspec` `Struct`, and for a stream item.

A subclass that declares an inherited array field again repeats the
`Shape`, because the new annotation replaces the old one.

When the annotation gives the number of dimensions, as in
`numpy.ndarray[tuple[int, int], numpy.dtype[numpy.uint8]]`, the `Shape`
must have as many. A `Shape((4, 6, 3))` under `tuple[int, int]` raises a
`TypeError` when the class is defined.

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
    1 -> scanner.scan = np.full((4, 6), 7, np.uint8)
    2 -> scan = scanner.scan
    3    scanner.read_into("scan", scan)
    4    with scanner.writing("scan") as scan:
    5        scan[0, :] = 255
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
      1    scanner.scan = np.full((4, 6), 7, np.uint8)
      2    scan = scanner.scan
      3 -> scanner.read_into("scan", scan)
      4    with scanner.writing("scan") as scan:
      5        scan[0, :] = 255
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
      1    scanner.scan = np.full((4, 6), 7, np.uint8)
      2    scan = scanner.scan
      3    scanner.read_into("scan", scan)
      4 -> with scanner.writing("scan") as scan:
      5 ->     scan[0, :] = 255
    |
  }
}
```

## 3. Read into an array you already have

A plain read gets fresh memory for a new array every time. If you read the
same field again and again, for example once per envelope, pass an array you
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
hand it, such as a scanner driver, [`writing`][sharedbox.SharedBox.writing]
saves you the extra copy. It gives you the field itself to fill:

```{.python}
--8<-- "docs/examples/store_arrays.py:writing"
```

The array starts out holding the field's current value. When the block
ends, other processes see the new values, and the box counts the write and
wakes everyone watching the field. That happens even if the block stops
halfway with an exception, and then the field keeps whatever the block
wrote before it stopped.

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

## 5. Store an array whose shape changes

A field declared with a fixed shape keeps that shape for good. When the
size changes from one write to the next, such as the label of an envelope
that holds a different number of lines each time, annotate the field with a
[`MaxShape`][sharedbox.MaxShape] instead. The field then stores the shape
of each array written to it, from 0 up to the maximum along each dimension,
and keeps room for the maximum, so choose the smallest maximum your data
allows:

```{.python}
--8<-- "docs/examples/store_arrays.py:max-shape"
```

A write takes an array of any shape that fits the maximum, and a read
returns an array of the shape stored last. A default array may be smaller
than the maximum, down to a dimension of 0 as `label` shows, because it
gives the field its first value and not its sizes. A write copies only the
elements in use, so a small array costs little to write even when the
maximum is large.

To fill the field in place, give [`writing`][sharedbox.SharedBox.writing]
the shape you are about to write, as above. The box stores that shape and
hands you an array of it under the same lock, so a reader never sees the
new shape with the old values. Without `shape`, `writing` hands you the
shape stored last. Fill all of the array you are handed, because its
contents are not the old value in the new shape. Elements beyond the ones
the last write stored, counted in C order, hold whatever an earlier and
larger write left there. When a dimension other than the first changes,
the old elements also end up at other positions. `read_into` takes fields
of a fixed shape only, and raises `TypeError` for this one.

## 6. Read with another library

A `SupportsDLPack` field reads back an object any DLPack library imports:

```{.python}
--8<-- "docs/examples/store_arrays.py:any-library"
```

To read fields back as an array type other than `numpy`'s and `torch`'s,
call [`register_array_type`][sharedbox.register_array_type] with the type
and its `from_dlpack` function before you define the box class.

## 7. Leave an array field empty

An array field annotated `X | None` holds either `None` or an array of its
sizes, which suits a value that isn't always there, such as the scan of an
envelope nobody has scanned yet:

```{.python}
--8<-- "docs/examples/store_arrays.py:optional"
```

Reading the field gives `None` while it holds no array. The field takes
its sizes from a `Shape` as any array field does, with the `| None`
outside the `Annotated`. It works as a record member and as a member of a
stream item too. Because the field may hold no array, `read_into` and
`writing` refuse it, and
[`receive_into`][sharedbox.SharedReceiveStream.receive_into] doesn't fill
it; a plain read or `receive` gives you a new array or `None`.

## 8. Handle the wrong shape or dtype

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
