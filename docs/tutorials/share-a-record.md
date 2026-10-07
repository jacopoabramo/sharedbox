---
icon: lucide/play
---

# Sharing a record between processes

In this tutorial you build a small script in which two processes share one
record: the first creates it, the second changes a value in it, and the
first reads the change. Such a record is a [box](../explanation/glossary.md#box), and on the way
you meet the three calls you will use most: `create`, `attach` and `close`.

## Before you start

!!! note "What you need"

    Python 3.11 or newer on Windows or Linux, and
    [`uv`](https://docs.astral.sh/uv/).

Make a project folder and add `sharedbox` to it:

```bash
uv init --bare --pin-python --python 3.11
uv add sharedbox
```

In the folder, create an empty file called `motor.py` and start it with
these imports:

```{.python}
--8<-- "docs/tutorials/motor.py:imports"
```

You won't need `queue` and `time` until the
[third tutorial](refer-to-another-box.md), but adding them now saves you
coming back to this line.

## 1. Define the record

Below the imports, describe the record as a class:

```{.python}
--8<-- "docs/tutorials/motor.py:motor"
```

This reads like a dataclass, and it works like one: each annotated
attribute is a [field](../explanation/glossary.md#field), and the value after `=` is its default.
The difference is that `Motor` subclasses
[`SharedBox`][sharedbox.SharedBox], so its fields live in shared memory
instead of inside one Python object.

Text needs a size limit, its [capacity](../explanation/glossary.md#capacity): `label` holds at most
32 bytes. Every field has a fixed size so that all processes agree on where
each value sits; [How a box is stored](../explanation/how-a-box-is-stored.md)
explains why that matters.

## 2. Change it from another process

Next, add the function the second process will run. It opens the box,
writes one field and lets go:

```{.python}
--8<-- "docs/tutorials/motor.py:move"
```

[`attach`][sharedbox.SharedBox.attach] opens the box named
`tutorial-motor`, which the first process creates in the next step. Setting
`motor.position` writes the new value straight into shared memory, where
every process can see it. [`close`][sharedbox.SharedBox.close] then drops
this process's [handle](../explanation/glossary.md#handle) on the box, and the box itself stays
for the processes still using it.

## 3. Create the box

Now add the function the first process runs. It creates the box, starts the
second process and reads what that process wrote:

```{.python}
--8<-- "docs/tutorials/motor.py:share"
```

[`create`][sharedbox.SharedBox.create] puts the box in a new
[segment](../explanation/glossary.md#segment) of shared memory under the name you give it. It
takes field values the way calling the class does, so here you set `label`
and leave `position` and `enabled` at their defaults.

The second process runs `move(10)`. Once `join` returns, that process has
finished, so reading `motor.position` finds the value it wrote.

When the `with` block ends it closes the box, and
[`unlink`][sharedbox.SharedBox.unlink] removes the name, so the next run
can create the box again. [How to name a box](../how-to/name-a-box.md)
shows other ways to choose a name.

## 4. Run it

Add these lines at the end of the file:

```python
if __name__ == "__main__":
    share()
```

You need them because the second process may import your script to find
`move`, and the `if` stops it from calling `share()` a second time. Now run
the script:

```bash
uv run motor.py
```

You should see:

```text
position set by the other process: 10
```

## What you built

You have a record in shared memory that two processes read and write: the
first created it, the second changed `position`, and the first read the
new value.

## Next steps

- [Reacting to changes](react-to-changes.md) is next: instead of reading
  the value after the other process ends, the first process sees each
  change the moment it happens.
- [How a box is stored](../explanation/how-a-box-is-stored.md) shows what
  the shared memory holds.
- [`SharedBox`][sharedbox.SharedBox] lists every type a field can have.
