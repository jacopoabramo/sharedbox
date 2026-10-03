---
icon: lucide/wrench
---

# How to store records

A [field](../explanation/glossary.md#field) can hold a whole record: a
dataclass, a `NamedTuple`, a fixed-length tuple, a `TypedDict`, an attrs
class or a `msgspec.Struct`. Each member needs a type a field could have
on its own, a [capacity](../explanation/glossary.md#capacity) included
where it needs one.

## 1. Declare the record and the field

```{.python}
--8<-- "docs/examples/store_records.py:declare"
```

## 2. Write and read the whole record

Assigning writes every member at once; reading builds a new instance of
the class:

```{.python}
--8<-- "docs/examples/store_records.py:write"
```

A read calls the class's constructor, so `__post_init__`, attrs
converters and validators run on every read. They must give the same value
when they run again on a value they already produced.

## 3. Change one member

A record read from the [box](../explanation/glossary.md#box) is a copy:
changing it changes nothing in the box. Read it, make the new record and
assign it back:

```{.python}
--8<-- "docs/examples/store_records.py:change-one-member"
```

A frozen dataclass or a `NamedTuple` makes the copy read-only, which
turns a forgotten assignment into an error.

## 4. Leave out a `TypedDict` key

A key marked `NotRequired` may be missing; it is missing again when the
record is read back:

```{.python}
--8<-- "docs/examples/store_records.py:optional-key"
```

A mapping with a key the `TypedDict` does not declare, or without a
required key, raises `TypeError`.

## What a record cannot hold

A class definition raises `TypeError` for a record that a read could not
rebuild: a dataclass or attrs field with `init=False`, an `InitVar`
without a default, or a generic record class. A record cannot hold a
[reference field](../explanation/glossary.md#reference-field).

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/store_records.py"
    ```
