---
icon: lucide/wrench
---

# How to send a box to another process

A process that didn't create a [box](../explanation/glossary.md#box) can get it in two ways: you
hand it over as an argument when the process starts, or the process opens
it by name with [`attach`][sharedbox.SharedBox.attach]. This guide shows
both with `multiprocessing`, and when to prefer which.

## 1. Pass the box as an argument

Hand the box to the new process like any other argument:

```{.python}
--8<-- "docs/examples/send_a_box_to_another_process.py:argument"
```

```{.python}
--8<-- "docs/examples/send_a_box_to_another_process.py:main"
```

This works however the child is started. `multiprocessing` can start a
child in three ways, its start methods; switch between them to see what the
child gets:

```d2 title="What the child gets"
...@diagrams/style
label: "With spawn or forkserver, the box travels as a pickle of its name and ids. The child unpickles it and opens the same shared memory."
grid-columns: 1
vertical-gap: 30
pic: "" {
  style.stroke-width: 0
  style.fill: transparent
  grid-rows: 2
  grid-columns: 3
  horizontal-gap: 110
  vertical-gap: 60
  parent: "parent's box" {class: step}
  pickle: "pickle: name and ids" {
    class: file
    tooltip: Only the box's name and the numbers that tell it apart from other boxes, not its values.
  }
  child: "child's box" {
    class: step
    tooltip: A new handle on the same data, which the child closes when it's done.
  }
  g1: {class: gap}
  data: "shared memory" {class: hardware}
  g2: {class: gap}
  parent -> pickle: "pickled"
  pickle -> child: "unpickled"
  parent -> data
  child -> data
}
code: "" {
  style.stroke-width: 0
  style.fill: transparent
  code: |python
    1 -> context = mp.get_context("spawn")
    2    child = context.Process(target=report, args=(status,))
    3    child.start()
  |
}
scenarios: {
  fork: {
    label: "With fork, the child starts as a copy of the parent and keeps using the parent's box object. Nothing is pickled."
    pic.pickle.style.opacity: 0.3
    (pic.parent -> pic.pickle)[0].style.opacity: 0.3
    (pic.pickle -> pic.child)[0].style.opacity: 0.3
    pic.child.label: "parent's box, copied"
    pic.child.tooltip: The parent's box object goes on working after the fork.
    code.code: |python
      1 -> context = mp.get_context("fork")
      2    child = context.Process(target=report, args=(status,))
      3    child.start()
    |
  }
}
```

`multiprocessing.shared_memory.SharedMemory` travels the same way. See
[`SharedBox`][sharedbox.SharedBox] for what the pickle holds, and the
[handle](../explanation/glossary.md#handle) entry for what a second box
object on the same data means.

## 2. In a pool, attach once per worker

Each time a box is unpickled, the child opens its [segment](../explanation/glossary.md#segment)
again. If you pass the box with every task of a pool, it gets opened once
per task, which is wasted work. Pass the box's name to the pool's
initializer instead, and attach there, once per worker:

```{.python}
--8<-- "docs/examples/send_a_box_to_another_process.py:pool"
```

`main` above starts the pool with `initargs=(status.name,)`.

## 3. Choose the start method

Windows only has `spawn`. On Linux the default is `fork` before
Python 3.14 and `forkserver` from 3.14 on. To choose one yourself, use
`multiprocessing.get_context`, as the script does.

!!! warning "Forking a box with callbacks can hang"
    If the box has callbacks connected to its
    [`events`][sharedbox.SharedBox.events], a child forked while a callback
    runs can hang, and a forked child forwards nothing from
    [`follow`][sharedbox.BoxEvents.follow] until it calls `follow` again.
    Use `spawn` or `forkserver` for such a box.

## Keeping a pickle for later

A pickled box only says which box it is; it doesn't hold the values, so it's
no good for saving them. Loading it after the box was removed raises
[`SegmentNotFoundError`][sharedbox.SegmentNotFoundError], and loading it
after a new box was created under the same name raises
[`SchemaMismatchError`][sharedbox.SchemaMismatchError]. To keep the values,
store a [`snapshot`][sharedbox.SharedBox.snapshot] instead.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/send_a_box_to_another_process.py"
    ```
