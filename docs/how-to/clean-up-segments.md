---
icon: lucide/wrench
---

# How to clean up segments

A [box](../explanation/glossary.md#box) is in shared memory, which outlives
the Python object you use it through. Close each box a process opens, and
remove the [segment](../explanation/glossary.md#segment)'s name once no
process needs the box any more.

## 1. Close each box you open

Leave a `with` block, or call [`close`][sharedbox.SharedBox.close]:

```{.python}
--8<-- "docs/examples/clean_up_segments.py:close"
```

`close` detaches this process's [handle](../explanation/glossary.md#handle)
and stops its [watcher](../explanation/glossary.md#watcher) thread; the
data stays for the other processes. Call it yourself when a callback
refers to the box, as the `lambda` above does: that callback keeps the box
alive until `close`.

## 2. Remove the name

[`unlink`][sharedbox.SharedBox.unlink] removes the name, so no process can
attach the box after that:

```{.python}
--8<-- "docs/examples/clean_up_segments.py:unlink"
```

Call it once, from the process that created the box;
[Closing and lifetime](../explanation/closing-and-lifetime.md#lifetime-the-same-rules-as-multiprocessingshared_memory)
says why. On Linux the segment stays until it is unlinked. On Windows
`unlink` does nothing, and the segment goes away when its last handle is
closed, so the same code runs on both.

## Remove a segment left by a crash

On Linux, a process that crashed before `unlink` leaves its segment behind
as a file in `/dev/shm`. List them:

```bash
ls /dev/shm/sharedbox.*
```

Creating a box under a name that is left over raises
[`SegmentExistsError`][sharedbox.SegmentExistsError], and its message says
whether the creator is still running. Remove it with `unlink` and the
name after `sharedbox.`, for example `Job.unlink("job-1")`, or delete the
file.

On Windows nothing is left behind: a segment goes away with the last
process that has it open.

## Release a lock left by a crash

A process that dies in the middle of a write leaves the box's write lock
taken. Every later read and write then waits for the class's
`lock_timeout` and raises [`LockTimeoutError`][sharedbox.LockTimeoutError],
whose message names the process that holds the lock. Once you know that
process is no longer running, call
[`force_unlock`][sharedbox.SharedBox.force_unlock] on a box attached to the
same segment, then write again:

```{.python}
--8<-- "docs/examples/clean_up_segments.py:force-unlock"
```

The write that timed out did not happen, so the field still holds its old
value until you write it again. Check that the writer has stopped first;
[`force_unlock`][sharedbox.SharedBox.force_unlock] says what happens if it
has not.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/clean_up_segments.py"
    ```
