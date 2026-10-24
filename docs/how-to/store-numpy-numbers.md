---
icon: lucide/wrench
---

# How to store numpy numbers

Numbers that come out of `numpy`, such as the largest of a row of scans or
a reading from a sensor driver, go into a box without converting them
first. An `int`, `float`, `complex` or `bool`
[field](../explanation/glossary.md#field) takes `numpy`'s scalars, and a field can also declare one of `numpy`'s
fixed-width types, so it takes exactly the bytes of that width. This guide
shows both, and what happens when a number doesn't fit.

## 1. Write numpy numbers to number fields

You assign a `numpy` scalar to an `int` or `float` field as you would a
Python number, here one taken out of an array:

```{.python}
--8<-- "docs/examples/store_numpy_numbers.py:input"
```

The field reads back the type its annotation names, whatever type you
wrote, so `parcels` reads back the `int` 7, not a `numpy.uint16`. An `int`
field takes any integer `numpy` makes, a `float` field any real number, and
a `complex` field any complex number. A `bool` field takes `numpy.bool_`, but neither an `int` nor a
`float` field takes a `bool`.

## 2. Declare a field of a fixed-width type

When the width matters, for a C program that reads the box or for a large
record, annotate the field with the `numpy` type itself:

```{.python}
--8<-- "docs/examples/store_numpy_numbers.py:declare"
```

`grams` takes 2 bytes and `reading` takes 4, and each reads back as its
`numpy` type. You can declare the integers `int8`, `int16`, `int32`,
`int64`, `uint8`, `uint16`, `uint32` and `uint64`, the floats `float16`,
`float32` and `float64`, `complex64` and `complex128`, `bool_`, and
`datetime64`, which section 4 covers.

A Python number works too: `scale.grams = 1200` stores
`numpy.uint16(1200)`, and a `float32` field rounds a Python `float` to the
nearest `float32`, as `numpy` does. A type checker such as `mypy` checks
each write against the annotation, so it reports `scale.grams = 1200` as an
error. A write of the declared type, as in the script, or of a value taken
out of an array passes the check.

`raw` is a `numpy.int64`, which stores the same bytes as an `int` field.
So do `numpy.float64` and `float`, `numpy.complex128` and `complex`, and
`numpy.bool_` and `bool`. Two classes with the same
[identity](../explanation/glossary.md#identity), one declaring `int` and
the other `numpy.int64`, attach to the same box, and each reads back its
own type.

## 3. Handle a number that doesn't fit

A fixed-width field refuses a number its width can't hold:

```{.python}
--8<-- "docs/examples/store_numpy_numbers.py:overflow"
```

If you assign one, you get an `OverflowError` and the field keeps its old
value. A `uint16` field refuses 70000 and -1. A `float32` or `float16`
field refuses a finite number past its range, such as `1e300`, rather than
storing an infinity as `numpy` does. NaN and the infinities themselves are
stored as they are. An `int` field refuses `numpy.uint64(2**63)`, as it
refuses any integer past 64 signed bits.

## 4. Store a time

A `numpy.datetime64` field stores a count of nanoseconds since 1970, which
covers the years 1678 to 2262:

```{.python}
--8<-- "docs/examples/store_numpy_numbers.py:time"
```

It takes a `datetime64` of any unit and reads back one in nanoseconds,
so the time you wrote in minutes comes back with nine digits after the
seconds. A time before 1678 or after 2262 raises an `OverflowError`.
`NaT`, `numpy`'s value for no time, is stored and reads back as `NaT`.

## 5. Write numpy numbers to a union

A union keeps a `numpy` number in its own kind of number:

```{.python}
--8<-- "docs/examples/store_numpy_numbers.py:union"
```

The `numpy.int64` 5 matches neither member's class, so it goes to the
first integer member, `int`, and reads back as `5`; the `numpy.float32` 0.5
goes to the first real member, `float`. If that member can't hold the
value, you get an `OverflowError`, and the box doesn't try the other
members.
[Field types](../explanation/field-types.md#unions) explains how a union
picks its member for other values.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/store_numpy_numbers.py"
    ```
