---
icon: lucide/wrench
---

# How to store a value with its time

A reading such as a temperature or a position often means little without the
time it was taken. A [field](../explanation/glossary.md#field) of type
[`Timed[T]`][sharedbox.Timed] keeps a value and its timestamp together, so
they are written in one go and a reader never sees a new value with an old
time. The timestamp counts nanoseconds since the Unix epoch, and it either
comes from the box at the moment of the write or is a time you already have,
such as one a device reported.

## 1. Declare a timed field

Wrap the field's type in `Timed`. The type inside can be anything a field
could hold on its own, so text still needs its
[capacity](../explanation/glossary.md#capacity):

```{.python}
--8<-- "docs/examples/store_a_value_with_its_time.py:declare"
```

A field always takes a `Timed`, so a default is a `Timed` too, and assigning a
plain `21.5` raises `TypeError`.

## 2. Let the write stamp the value

[`Timed.now`][sharedbox.Timed.now] asks the write for the time. The box reads
the system clock once the write holds the box's lock, so the stamps of writes
from any process follow the order in which the writes happened:

```{.python}
--8<-- "docs/examples/store_a_value_with_its_time.py:stamp"
```

`timestamp_ns` is the stored integer, and the `timestamp` property gives the
same time in seconds as a `float`, the form `time.time()` returns.

## 3. Keep a time you already have

A `Timed` built with a timestamp of its own is stored as it is, which keeps
the time a device or another clock gave the value:

```{.python}
--8<-- "docs/examples/store_a_value_with_its_time.py:given"
```

!!! warning "The timestamp `-2**63` asks for a stamp"
    `Timed.now` works by storing `-2**63`, the smallest 64-bit integer, which
    the write replaces with the clock. A time of exactly `-2**63` that you pass
    yourself is therefore replaced too, and can't be stored.

## 4. Put timed values in collections, records and streams

A `Timed` can go wherever a record can: in a list, as a dict's value, inside a
record or an optional, and as a stream's item. Every `Timed.now` in one write
gets the same time, because the clock is read once per write:

```{.python}
--8<-- "docs/examples/store_a_value_with_its_time.py:collections"
```

A stream stamps each item when it is sent:

```{.python}
--8<-- "docs/examples/store_a_value_with_its_time.py:stream"
```

A `Timed` can't be a set's element or a dict's key, because two equal values
taken at different times would count as different keys; defining such a field
raises `TypeError`.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/store_a_value_with_its_time.py"
    ```
