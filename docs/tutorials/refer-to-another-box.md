---
icon: lucide/play
---

# Referring to another box

A [box](../explanation/glossary.md#box) can point at another box, the way an attribute of one
Python object can hold another object. In this tutorial you make a `Stage`
that points at a `Motor`, connect a callback that hears about changes inside
whichever motor the stage points at, and then switch the stage to a second
motor.

## Before you start

!!! note "What you need"

    The script `motor.py` from [Reacting to changes](react-to-changes.md).

## 1. Define the stage

Add this class below `react`:

```{.python}
--8<-- "docs/tutorials/motor.py:stage"
```

The `motor` field doesn't copy a motor's values into the stage. It is a
[reference field](../explanation/glossary.md#reference-field): it stores which `Motor` the stage
points at, and the motor keeps its values in its own shared memory. The
`| None` lets the field be empty, which is also its default here.
[Reference fields](../explanation/references.md) explains what exactly the
field stores.

## 2. Follow the motor

Add this function below `Stage`:

```{.python}
--8<-- "docs/tutorials/motor.py:follow"
```

The function creates two motors, `x` and `y`, and a stage that points at
`x`. [`events.follow("motor")`][sharedbox.BoxEvents.follow] gives you
signals like a motor's own `events`, and each one fires when the motor that
`stage.motor` currently points at changes. The callback runs on a
background thread, and printing from two threads at once can mix their
lines, so the callback only puts each new position on a queue. The main
thread takes the positions from there and prints them.

Assigning `y` to `stage.motor` makes the callback follow `y` instead of
`x`.

!!! warning "The switch to a new box takes a moment"
    After you assign `y` to `stage.motor`, a background thread moves the
    following from `x` to `y`, shortly after the assignment returns. A write
    to `y` made before that is missed by the callback. That is why the
    script waits half a second before it writes to `y`; in your own code,
    leave the same gap or don't rely on that first write.

## 3. Run it

In the `if` block at the end of the file, add the highlighted line:

```{.python hl_lines="4"}
--8<-- "docs/tutorials/motor.py:main"
```

Run the script:

```bash
uv run motor.py
```

The last two lines are new: the callback heard the write to `x`, then the
write to `y`:

```text
position set by the other process: 10
position 0 -> 20
watched 20
motor position 1
motor position 7
```

## What you built

You have a stage that points at a motor, and a callback that keeps hearing
about the stage's motor even after you point the stage at a different one.

## Next steps

- [How to follow a whole reference graph](../how-to/follow-a-whole-reference-graph.md)
  shows how to follow every box the stage reaches, not just one field, by
  calling [`follow`][sharedbox.BoxEvents.follow] without a field name.
- [Reference fields](../explanation/references.md) explains what a reference
  stores and how it can break.
- [Limits](../explanation/limits.md#following-boxes) says what following
  costs: one thread for each box you follow.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/tutorials/motor.py"
    ```
