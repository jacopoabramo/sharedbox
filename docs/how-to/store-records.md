---
icon: lucide/wrench
---

# How to store records

When several values belong together, such as an x and a y, you can keep them
in one [field](../explanation/glossary.md#field) as a record: a dataclass, a `NamedTuple`, a
fixed-length tuple, a `TypedDict`, an `attrs` class or a `msgspec.Struct`.
Each member can have any type a field could have on its own, so a text
member needs its [capacity](../explanation/glossary.md#capacity) too.

## 1. Declare the record and the field

Define the record class as usual, then use it as a field's type:

```{.python}
--8<-- "docs/examples/store_records.py:declare"
```

## 2. Write and read the whole record

When you assign a record, every member is written at once, so a reader sees
all of the new values or none of them. Reading builds a new instance of the
class:

```{.python}
--8<-- "docs/examples/store_records.py:write"
```

!!! warning "Your constructor runs on every read"
    Each read calls the class's constructor, so `__post_init__`, `attrs`
    converters and validators run every time. Make sure they give back the
    same value when they run on a value they produced before, or a read
    won't match what you wrote.

## 3. Change one member

A record you read from the [box](../explanation/glossary.md#box) is a copy, so changing it
changes nothing in the box. To change one member, read the record, make a
new one with the change, and assign it back:

```{.python}
--8<-- "docs/examples/store_records.py:change-one-member"
```

If you use a frozen dataclass or a `NamedTuple`, the copy is read-only, so
forgetting to assign it back gives you an error instead of a silent no-op.

## 4. Leave out a `TypedDict` key

A key marked `NotRequired` can be left out, and it is left out again when you
read the record back:

```{.python}
--8<-- "docs/examples/store_records.py:optional-key"
```

Assigning a mapping with a key the `TypedDict` doesn't declare, or without a
required key, raises `TypeError`.

## What a record cannot hold

A read has to rebuild the record from its stored members, so a record that
can't be rebuilt that way is refused with `TypeError` as soon as you define
the box class. That covers a dataclass or `attrs` field with `init=False`,
an `InitVar` without a default, and a generic record class. A record also
can't hold a [reference field](../explanation/glossary.md#reference-field).

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/store_records.py"
    ```
