---
icon: lucide/play
---

# Referring to another box

A [box](../explanation/glossary.md#box) can refer to another box. In this
tutorial a `Stage` refers to one `Motor`, a callback follows changes inside
whichever motor the stage refers to, and the stage then switches to a
second motor.

## Before you start

!!! note "What you need"

    The script `motor.py` from [Reacting to changes](react-to-changes.md).

## 1. Define the stage

Add this class below `react`:

```{.python}
--8<-- "docs/tutorials/motor.py:stage"
```

`motor` is a [reference field](../explanation/glossary.md#reference-field):
it stores which `Motor` the stage refers to, not the motor's values. The
motor keeps its own shared memory. `| None` lets the field be empty, which
is its default here. [References](../explanation/references.md) explains
what the field stores.

## 2. Follow the motor

Add this function below `Stage`:

```{.python}
--8<-- "docs/tutorials/motor.py:follow"
```

The function creates two motors, `x` and `y`, and a stage that refers to
`x`. [`events.follow("motor")`][sharedbox.BoxEvents.follow] returns the
signals of `Motor`, emitted for whichever motor `stage.motor` refers to
when the change happens. The callback runs on a background thread, so it
puts each new position on a queue, and the main thread takes it from there.

Assigning `y` to `stage.motor` moves the forwarding from `x` to `y`. That
happens on a background thread shortly after the assignment, and the
callback does not see a write to `y` made before then. The script waits
half a second before it writes to `y`.

## 3. Run it

Add the highlighted line at the end of the file:

```{.python hl_lines="4"}
--8<-- "docs/tutorials/motor.py:main"
```

Run the script:

```bash
uv run motor.py
```

```text
position set by the other process: 10
position 0 -> 20
watched 20
motor position 1
motor position 7
```

## What you built

A stage that refers to a motor, and a callback that follows the stage to
whichever motor it refers to, before and after the reference changes.

## Next steps

- [How to follow a whole reference graph](../how-to/follow-a-whole-reference-graph.md):
  [`follow`][sharedbox.BoxEvents.follow] called without a field follows
  every box the stage reaches through its reference fields.
- [References](../explanation/references.md) explains what a reference
  stores and why it can break.
- [Limits](../explanation/limits.md#following-boxes) says what following
  costs: one thread per followed box.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/tutorials/motor.py"
    ```
