---
icon: lucide/play
---

# Sharing a record between processes

In this tutorial you define a record with three
[fields](../explanation/glossary.md#field), create it in shared memory, and
change one field from a second process. Such a record is a
[box](../explanation/glossary.md#box).

## Before you start

!!! note "What you need"

    Python 3.11 or newer on Windows or Linux, and
    [`uv`](https://docs.astral.sh/uv/).

Make a project folder and add sharedbox to it:

```bash
uv init --bare --pin-python --python 3.11
uv add sharedbox
```

In the folder, make an empty file called `motor.py`. Start it with these
imports:

```{.python}
--8<-- "docs/tutorials/motor.py:imports"
```

The [third tutorial](refer-to-another-box.md) uses `queue` and `time`.

## 1. Define the record

Add a class below the imports:

```{.python}
--8<-- "docs/tutorials/motor.py:motor"
```

`Motor` is a subclass of [`SharedBox`][sharedbox.SharedBox]. Each annotated
attribute is a field, and the value after `=` is its default. A `str`
field needs a [capacity](../explanation/glossary.md#capacity): the most
bytes it can hold, here 32. [How a box is stored](../explanation/how-a-box-is-stored.md)
explains why every field has a fixed size.

## 2. Change it from another process

Add a function that opens the box and writes one field:

```{.python}
--8<-- "docs/tutorials/motor.py:move"
```

[`attach`][sharedbox.SharedBox.attach] opens the box called
`tutorial-motor`, which another process creates. Setting `position` writes
the value into shared memory. [`close`][sharedbox.SharedBox.close] closes
this process's [handle](../explanation/glossary.md#handle) on the box; the
box itself stays.

## 3. Create the box

Add the function that creates the box and starts the second process:

```{.python}
--8<-- "docs/tutorials/motor.py:share"
```

[`create`][sharedbox.SharedBox.create] makes a new
[segment](../explanation/glossary.md#segment) of shared memory under the
name it is given, and takes field values as calling the class does;
`position` and `enabled` keep their defaults. The `with` block closes the
box at its end, and [`unlink`][sharedbox.SharedBox.unlink] removes the
name, so the next run can create it again.
[How to name a box](../how-to/name-a-box.md) shows the other ways to
choose the name.

The second process runs `move(10)`. After `join`, reading `motor.position`
reads the segment again and finds the value that process wrote.

## 4. Run it

Add these lines at the end of the file:

```python
if __name__ == "__main__":
    share()
```

The second process may import the script to find `move`, and the `if`
keeps it from calling `share()` too. Run the script:

```bash
uv run motor.py
```

```text
position set by the other process: 10
```

## What you built

A record in shared memory that two processes read and write: one created
it, the other changed a field, and the first read the change.

## Next steps

- [Reacting to changes](react-to-changes.md) is the next tutorial: the
  first process sees the write as it happens, instead of reading it
  afterwards.
- [How a box is stored](../explanation/how-a-box-is-stored.md) explains
  what the segment holds.
- [`SharedBox`][sharedbox.SharedBox] lists the types a field can have.
