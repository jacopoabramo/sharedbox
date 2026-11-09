---
icon: lucide/wrench
---

# How to change a field from several processes

Several processes can write the same [field](../explanation/glossary.md#field)
of a [box](../explanation/glossary.md#box), and each write is safe: no reader
sees half a value, and the last writer wins. A write that you compute from
what the box holds is different, because reading and writing are two steps,
and another process can write in between. This guide shows how to keep such
a change whole with [`locked`][sharedbox.SharedBox.locked].

## 1. See the lost update

`job.count += 1` reads `count`, adds 1 and writes the result back. Two
processes running it at the same time can both read 5 and both write 6:

| time | process A | process B | `count` |
| --- | --- | --- | --- |
| 1 | reads 5 | | 5 |
| 2 | | reads 5 | 5 |
| 3 | writes 6 | | 6 |
| 4 | | writes 6 | 6, not 7 |

Each write was fine, but B computed its 6 from a 5 that A had already
replaced, so A's increment is gone. Two workers counting 10000 times each
often end below 20000:

```{.python}
--8<-- "docs/examples/change_a_field_from_several_processes.py:lost"
```

[`update(count=job.count + 1)`][sharedbox.SharedBox.update] doesn't help,
because Python computes `job.count + 1` before `update` runs, so the read is
still a separate step.

## 2. Read and write inside `locked`

Every write takes the box's lock, the
[sequence lock](../explanation/glossary.md#sequence-lock), which makes other
writers wait and readers retry until the write is done.
[`locked`][sharedbox.SharedBox.locked] holds that lock for a whole `with`
block, so no other process can write between your read and your write:

```{.python}
--8<-- "docs/examples/change_a_field_from_several_processes.py:locked"
```

Inside the block you use `j`, the object that `as j` names. It has the same
fields as `job`, and a type checker sees the same types. Now two workers end
at 20000 every time:

```{.python}
--8<-- "docs/examples/change_a_field_from_several_processes.py:run"
```

When the block ends, other processes see all of its changes at the same
moment, as they see an `update`. A [watcher](../explanation/glossary.md#watcher)
or a signal of [`events`][sharedbox.SharedBox.events] reports each field the
block assigned once, with its value from after the block, however often the
block assigned it.

## 3. Check a value before you change it

The block can decide what to write from what it reads. Here two callers try
to claim the same job, and only the first one gets it:

```{.python}
--8<-- "docs/examples/change_a_field_from_several_processes.py:claim"
```

```{.python}
--8<-- "docs/examples/change_a_field_from_several_processes.py:claimed"
```

## 4. Leave the box as it was when the block fails

If the block raises, every field it wrote goes back to its value from before
the block, nobody is told about a change, and the exception carries on:

```{.python}
--8<-- "docs/examples/change_a_field_from_several_processes.py:rollback"
```

!!! warning "Every process waits while the block runs"

    While the block holds the lock, reads and writes of the box wait in every
    process, and one that waits longer than its lock timeout, 5 s unless the
    class sets `lock_timeout`, raises
    [`LockTimeoutError`][sharedbox.LockTimeoutError]. A block that
    reads, decides and writes takes a couple of microseconds. Do any slow
    work, and every `await`, before or after the block, never inside it.

!!! warning "Use only the block's object inside the block"

    In the thread that holds the lock, reading or writing `job` itself
    raises `RuntimeError`, and so does using `j` after the block or from
    another thread. Methods that wait, watch or close, such as
    [`watch`][sharedbox.SharedBox.watch] or
    [`close`][sharedbox.SharedBox.close], raise `TypeError` on `j`.

    Only `job` and `j` know that your thread holds the lock. A second box
    you open on the same name in the same thread waits for the lock like
    any other process, and raises `LockTimeoutError`. So does a box you
    reach through a [reference field](../explanation/glossary.md#reference-field)
    that points back at `job`. Don't start a child process with `fork`
    inside the block either: the child would get a copy of a lock it
    doesn't hold.

Locking two boxes needs one order. If one process locks box A and then box
B, while another locks B and then A, each ends up waiting for the box the
other holds, and neither moves until one of them raises `LockTimeoutError`.
Lock boxes in the same order in every process.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/change_a_field_from_several_processes.py"
    ```
