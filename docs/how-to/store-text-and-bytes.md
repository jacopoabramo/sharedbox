---
icon: lucide/wrench
---

# How to store text and bytes

A `str` or `bytes` [field](../explanation/glossary.md#field) needs a
[capacity](../explanation/glossary.md#capacity): the most bytes it can
hold. The box sets that room aside when it is created, so a value can never
grow past it.

## 1. Declare the field with a capacity

Put [`Capacity`][sharedbox.Capacity] in the annotation, inside
`Annotated`:

```{.python}
--8<-- "docs/examples/store_text_and_bytes.py:declare"
```

## 2. Write and read values

Assign and read the field as any other:

```{.python}
--8<-- "docs/examples/store_text_and_bytes.py:write"
```

A `bytes` field also takes a `bytearray` or a `memoryview`, and reading it
gives `bytes`.

## 3. Handle a value that is too long

A value longer than the capacity raises `ValueError`, and the field keeps
its old value:

```{.python}
--8<-- "docs/examples/store_text_and_bytes.py:too-long"
```

## 4. Count bytes, not characters

A capacity counts the bytes of the UTF-8 encoding, and a character takes 1
to 4 of them:

```{.python}
--8<-- "docs/examples/store_text_and_bytes.py:bytes-not-characters"
```

For text of up to `n` characters in any language, give the field a
capacity of `4 * n`. For text you know is ASCII, `n` is enough.

## 5. Cut a value to fit

To store the start of a longer text instead of refusing it, cut its
encoding to the capacity and drop a character the cut split in two:

```{.python}
--8<-- "docs/examples/store_text_and_bytes.py:fit"
```

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/store_text_and_bytes.py"
    ```
