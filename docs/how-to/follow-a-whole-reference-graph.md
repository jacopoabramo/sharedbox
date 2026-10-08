---
icon: lucide/wrench
---

# How to follow a whole reference graph

Boxes can point at boxes that point at further boxes: a stage at a motor, the
motor at an encoder. When a [box](../explanation/glossary.md#box)'s
[reference fields](../explanation/glossary.md#reference-field) lead on like this, they form a
graph. This guide shows how one callback can hear about a change anywhere in
it.

## Before you start

!!! note "What you need"

    Classes with reference fields. This guide uses a stage that refers to a
    motor, which refers to an encoder:

    ```{.python}
    --8<-- "docs/examples/follow_a_whole_reference_graph.py:classes"
    ```

[Referring to another box](../tutorials/refer-to-another-box.md) follows a
single reference field; here you follow all of them at once.

## 1. Follow every reference

The script creates an encoder, a motor that points at it and a stage that
points at the motor. Call [`follow`][sharedbox.BoxEvents.follow] on the
stage's events without naming a field, then connect to the
[`nested`][sharedbox.BoxEvents.nested] signal:

```{.python}
--8<-- "docs/examples/follow_a_whole_reference_graph.py:follow-all"
```

Your callback gets `(path, new, old)`, where `path` lists the field names
on the way from the stage to the field that changed. When any process points
a reference field at a different box, the following moves to the new box by
itself.

The callbacks run on background threads, one for each box you follow. Hand
the values over to your own thread, as the queue does here, or connect with
`thread="main"`, as the docstring of [`events`][sharedbox.SharedBox.events]
describes.

## 2. Follow one path instead

If you only care about one box deep in the graph, call `follow` with a
field at each level instead. Each call gives you the signals of the class
that field points at:

```{.python}
--8<-- "docs/examples/follow_a_whole_reference_graph.py:follow-one-path"
```

## 3. Stop following

[`unfollow`][sharedbox.BoxEvents.unfollow] with a field stops what `follow`
with that field started, and without a field it stops everything, as the
script does before step 2. Closing the box stops all following too.

Following isn't free: each box you follow takes one
[waiter slot](../explanation/glossary.md#waiter-slot) and one thread.
[Limits](../explanation/limits.md#following-boxes) says what that adds up
to for a large graph.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/follow_a_whole_reference_graph.py"
    ```
