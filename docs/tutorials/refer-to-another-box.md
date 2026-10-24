---
icon: lucide/play
---

# Referring to another box

A [box](../explanation/glossary.md#box) can point at another box, the way an attribute of one
Python object can hold another object. In this tutorial you make a
`Carrier` whose current stop is a `Mailbox`, connect a callback that hears
about changes inside whichever mailbox the carrier stops at, and then move
the carrier on to a second mailbox.

## Before you start

!!! note "What you need"

    The script `mailbox.py` from [Reacting to changes](react-to-changes.md).

## 1. Define the carrier

Add this class below `react`:

```{.python}
--8<-- "docs/tutorials/mailbox.py:carrier"
```

The `stop` field doesn't copy a mailbox's values into the carrier. It is a
[reference field](../explanation/glossary.md#reference-field): it stores which `Mailbox` the carrier
points at, and the mailbox keeps its values in its own shared memory. The
`| None` lets the field be empty, which is also its default here.
[Reference fields](../explanation/references.md) explains what exactly the
field stores.

## 2. Follow the mailbox

Add this function below `Carrier`:

```{.python}
--8<-- "docs/tutorials/mailbox.py:follow"
```

The function creates two mailboxes, `north` and `south`, and a carrier
whose stop is `north`. [`events.follow("stop")`][sharedbox.BoxEvents.follow]
gives you signals like a mailbox's own `events`, and each one fires when the
mailbox that `carrier.stop` currently points at changes. The callback runs on a
background thread, and printing from two threads at once can mix their
lines, so the callback only puts each new count on a queue. The main
thread takes the counts from there and prints them.

Assigning `south` to `carrier.stop` makes the callback follow `south`
instead of `north`.

!!! warning "The switch to a new box takes a moment"
    After you assign `south` to `carrier.stop`, a background thread moves
    the following from `north` to `south`, shortly after the assignment
    returns. A write to `south` made before that is missed by the callback.
    That is why the script waits half a second before it writes to `south`;
    in your own code,
    leave the same gap or don't rely on that first write.

## 3. Run it

In the `if` block at the end of the file, add the highlighted line:

```{.python hl_lines="4"}
--8<-- "docs/tutorials/mailbox.py:main"
```

Run the script:

```bash
uv run mailbox.py
```

The last two lines are new: the callback heard the write to `north`, then
the write to `south`:

```text
letters left by the carrier: 2
letters 0 -> 3
watched 3
letters at the stop 1
letters at the stop 7
```

## What you built

You have a carrier that points at a mailbox, and a callback that keeps
hearing about the carrier's stop even after you move the carrier on to a
different one.

## Next steps

- [How to follow a whole reference graph](../how-to/follow-a-whole-reference-graph.md)
  shows how to follow every box the carrier reaches, not just one field, by
  calling [`follow`][sharedbox.BoxEvents.follow] without a field name.
- [Reference fields](../explanation/references.md) explains what a reference
  stores and how it can break.
- [Limits](../explanation/limits.md#following-boxes) says what following
  costs: one thread for each box you follow.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/tutorials/mailbox.py"
    ```
