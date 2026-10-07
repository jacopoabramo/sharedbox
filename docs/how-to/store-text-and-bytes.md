---
icon: lucide/wrench
---

# How to store text and bytes

Text and bytes vary in length, but every [field](../explanation/glossary.md#field) of a
[box](../explanation/glossary.md#box) has a fixed size. So when you declare a `str` or `bytes`
field, you also say how much room it gets, its [capacity](../explanation/glossary.md#capacity):
the most bytes it can hold. This guide shows how to choose it and what
happens when a value doesn't fit.

## 1. Declare the field with a capacity

Wrap the type in `Annotated` and add a [`Capacity`][sharedbox.Capacity]:

```{.python}
--8<-- "docs/examples/store_text_and_bytes.py:declare"
```

## 2. Write and read values

From then on you use the field like any other attribute:

```{.python}
--8<-- "docs/examples/store_text_and_bytes.py:write"
```

You can also assign a `bytearray` or a `memoryview` to a `bytes` field;
reading it back always gives you `bytes`.

## 3. Handle a value that is too long

If you assign a value longer than the capacity, you get a `ValueError` and
the field keeps its old value, so a reader never sees half of it:

```{.python}
--8<-- "docs/examples/store_text_and_bytes.py:too-long"
```

## 4. Count bytes, not characters

The capacity counts bytes of the UTF-8 encoding, not characters, and a
character takes between 1 and 4 bytes. Text with accents or other scripts
fills the room faster than it looks:

```{.python}
--8<-- "docs/examples/store_text_and_bytes.py:bytes-not-characters"
```

To be sure `n` characters of any language fit, give the field a capacity of
`4 * n`. If you know the text is plain ASCII, `n` is enough.

## 5. Cut a value to fit

Sometimes you'd rather keep the start of a long text than refuse it. Cut
its encoding to the capacity, and drop any character the cut split in two,
as this helper does:

```{.python}
--8<-- "docs/examples/store_text_and_bytes.py:fit"
```

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/store_text_and_bytes.py"
    ```
