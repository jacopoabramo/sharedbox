---
icon: lucide/wrench
---

# How to follow a whole reference graph

A box whose [reference fields](../explanation/glossary.md#reference-field)
lead to other boxes, which have reference fields of their own, forms a
graph. This guide listens to changes anywhere in that graph with one
callback.

## Before you start

!!! note "What you need"

    Classes with reference fields. This guide uses a stage that refers to a
    motor, which refers to an encoder:

    ```{.python}
    --8<-- "docs/examples/follow_a_whole_reference_graph.py:classes"
    ```

[Referring to another box](../tutorials/refer-to-another-box.md) follows a
single reference field; this guide follows all of them.

## 1. Follow every reference

The script creates an encoder, a motor that refers to it and a stage that
refers to the motor. Call [`follow`][sharedbox.BoxEvents.follow] on the
stage's events without a field, then connect to the
[`nested`][sharedbox.BoxEvents.nested] signal:

```{.python}
--8<-- "docs/examples/follow_a_whole_reference_graph.py:follow-all"
```

`nested` is emitted as `(path, new, old)`, where `path` holds the field
names from the stage to the field that changed. When any process assigns
another box to a reference field, following moves to the new box.

The callbacks run on background threads, one per followed box. Take the
values to your own thread, as the queue does here, or connect with
`thread="main"` as the docstring of
[`events`][sharedbox.SharedBox.events] describes.

## 2. Follow one path instead

To listen to one box deep in the graph, call `follow` with a field at each
level. Each call returns the signals of the class the field refers to:

```{.python}
--8<-- "docs/examples/follow_a_whole_reference_graph.py:follow-one-path"
```

## 3. Stop following

[`unfollow`][sharedbox.BoxEvents.unfollow] with a field stops what
`follow` with that field started; without a field it stops everything, as
the script does before step 2. Closing the box also stops all following.

Each followed box takes one
[waiter slot](../explanation/glossary.md#waiter-slot) and one thread;
[Limits](../explanation/limits.md#following-boxes) says what that costs
for a large graph.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/follow_a_whole_reference_graph.py"
    ```
