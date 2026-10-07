---
icon: lucide/wrench
---

# How to clean up segments

A [box](../explanation/glossary.md#box) lives in shared memory, and that memory outlives the Python
object you use it through. So cleaning up takes two steps: each process
closes the boxes it opened, and once no process needs a box any more, you
remove the name of its [segment](../explanation/glossary.md#segment). This guide also shows how to
recover when a process crashed before cleaning up.

## 1. Close each box you open

Leave a `with` block, or call [`close`][sharedbox.SharedBox.close] yourself:

```{.python}
--8<-- "docs/examples/clean_up_segments.py:close"
```

`close` lets go of this process's [handle](../explanation/glossary.md#handle) and stops its
[watcher](../explanation/glossary.md#watcher) thread, while the data stays for the other
processes.

!!! warning "A callback that uses the box keeps it open"
    A callback that refers to the box, as the `lambda` above does, keeps the
    box alive, so garbage collection never closes it for you. Call `close`
    yourself on such a box.

## 2. Remove the name

[`unlink`][sharedbox.SharedBox.unlink] removes the name, so no process can
attach the box from then on:

```{.python}
--8<-- "docs/examples/clean_up_segments.py:unlink"
```

Call it once, from the process that created the box;
[Closing and lifetime](../explanation/closing-and-lifetime.md#lifetime-the-same-rules-as-multiprocessingshared_memory)
says why. On Linux the segment stays until it is unlinked. On Windows
`unlink` does nothing, because the segment goes away by itself when its last
handle is closed. Either way you write the same code, and it works on both.

## Remove a segment left by a crash

On Linux, a process that crashed before calling `unlink` leaves its segment
behind as a file in `/dev/shm`. You can list them:

```bash
ls /dev/shm/sharedbox.*
```

If you try to create a box under a name that is left over, you get
[`SegmentExistsError`][sharedbox.SegmentExistsError], and its message says
whether the process that created it is still running. If it isn't, remove
the segment with `unlink` and the part of the file name after `sharedbox.`,
for example `Job.unlink("job-1")`, or simply delete the file.

On Windows nothing is left behind: a segment goes away with the last
process that has it open.

## Release a lock left by a crash

A process that dies in the middle of a write leaves the box's write lock
taken. From then on, every read and write waits for the class's
`lock_timeout` and then raises
[`LockTimeoutError`][sharedbox.LockTimeoutError], whose message names the
process that holds the lock. Once you know that process is gone, call
[`force_unlock`][sharedbox.SharedBox.force_unlock] on any box attached to
the same segment, then write again:

```{.python}
--8<-- "docs/examples/clean_up_segments.py:force-unlock"
```

The write that timed out didn't happen, so the field keeps its old value
until you write it again.

!!! warning "Only unlock a lock whose owner is gone"
    `force_unlock` doesn't check that the process holding the lock has
    stopped. If it is still running, its half-finished write becomes
    visible to readers. Check the pid in the error message first;
    [`force_unlock`][sharedbox.SharedBox.force_unlock] has the details.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/clean_up_segments.py"
    ```
