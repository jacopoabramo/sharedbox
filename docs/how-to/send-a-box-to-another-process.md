---
icon: lucide/wrench
---

# How to send a box to another process

A process that did not create a [box](../explanation/glossary.md#box) can
get it in two ways: as an argument when it starts, or by name with
[`attach`][sharedbox.SharedBox.attach]. This guide uses `multiprocessing`.

## 1. Pass the box as an argument

Give the box to the process like any other argument:

```{.python}
--8<-- "docs/examples/send_a_box_to_another_process.py:argument"
```

```{.python}
--8<-- "docs/examples/send_a_box_to_another_process.py:main"
```

With the `spawn` or `forkserver` start method, `multiprocessing` pickles the
arguments. The pickle holds only the box's name and which box it is (see
[`SharedBox`][sharedbox.SharedBox]), and unpickling it attaches a new
[handle](../explanation/glossary.md#handle): an independent box on the
same data, which the child closes when it is done. `multiprocessing.shared_memory.SharedMemory` is sent the same way.

With the `fork` start method nothing is pickled: the child uses the
parent's box object, which keeps working after the fork.

## 2. In a pool, attach once per worker

Each unpickle opens the segment again, so a box passed with every task is
opened once per task. Pass the box's name to the pool's initializer
instead, and attach there:

```{.python}
--8<-- "docs/examples/send_a_box_to_another_process.py:pool"
```

`main` above starts the pool with `initargs=(status.name,)`.

## 3. Choose the start method

Windows only has `spawn`. On Linux the default is `fork` before
Python 3.14 and `forkserver` from 3.14 on. Ask for one with
`multiprocessing.get_context`, as the script does.

Use `spawn` or `forkserver` when the box has callbacks connected to its
[`events`][sharedbox.SharedBox.events]. A child forked while a callback
runs can hang, and a forked child forwards nothing from
[`follow`][sharedbox.BoxEvents.follow] until it calls `follow` again.

## Do not store a pickle

A pickled box names a box that exists now; it does not hold the values.
Loading it after the box was removed raises
[`SegmentNotFoundError`][sharedbox.SegmentNotFoundError], and loading it
after a new box was created under the same name raises
[`SchemaMismatchError`][sharedbox.SchemaMismatchError]. To keep the values,
store a [`snapshot`][sharedbox.SharedBox.snapshot].

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/send_a_box_to_another_process.py"
    ```
