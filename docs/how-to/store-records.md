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
all of the new values or none of them. Reading gives you an instance of the
class, a new one on every read unless the class is frozen (see
[When a read builds a new value](../explanation/field-types.md#when-a-read-builds-a-new-value)):

```{.python}
--8<-- "docs/examples/store_records.py:write"
```

!!! warning "Your constructor runs when a read builds the record"
    A read builds the record by calling its class, so `__post_init__`,
    `attrs` converters and validators run on every read when the record's
    class isn't frozen, and only on the first read after each write when it
    is. Make sure running them a second time changes nothing: a converter
    that rounds a float, for example, must leave an already rounded float as
    it is, or a read won't match what you wrote.

## 3. Change one member

Changing a record you read from the [box](../explanation/glossary.md#box)
changes nothing in the box. To change one member, read the record, make a
new one with the change, and assign it back:

```{.python}
--8<-- "docs/examples/store_records.py:change-one-member"
```

If you use a frozen dataclass or a `NamedTuple`, the record you read is
read-only, so forgetting to assign it back gives you an error instead of a
silent no-op.

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
