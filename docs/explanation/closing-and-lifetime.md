---
icon: lucide/lightbulb
---

# Closing and lifetime

A [box](glossary.md#box) has two lifetimes: the Python object you use, and
the shared memory behind it, which other processes may still be using. This
page first explains how long that memory lives and who removes it, which is
what you need day to day. Then it explains how
[`close`][sharedbox.SharedBox.close] stays safe while other threads of your
process are still using the box, which matters if you work on `sharedbox`
itself or wonder why closing never crashes a running read.

## Lifetime: the same rules as `multiprocessing.shared_memory`

Closing a box only lets go of it in your process; it never destroys the
data. Step through the life of one segment that two processes use:

```d2 title="The life of a segment"
...@diagrams/style
grid-rows: 1
horizontal-gap: 40
created: "A creates the box" {
  class: current
  tooltip: A creates the segment under a name and holds one handle on it.
}
shared: "B attaches" {class: step}
a_closed: "A closes" {class: step}
unlinked: "A unlinks the name" {class: step}
freed: "B closes: memory freed" {class: step}
created -> shared -> a_closed -> unlinked -> freed
scenarios: {
  shared: {
    created.class: done
    shared.class: current
    shared.tooltip: B opens the segment by name and gets its own handle on the same memory.
  }
  a_closed: {
    created.class: done
    shared.class: done
    a_closed.class: current
    a_closed.tooltip: A's handle is gone, but the data stays, because B still uses it.
  }
  unlinked: {
    created.class: done
    shared.class: done
    a_closed.class: done
    unlinked.class: current
    unlinked.tooltip: On Linux the name disappears, like deleting an open file, and nobody new can attach. On Windows there is nothing to remove, and unlink does nothing.
  }
  freed: {
    created.class: done
    shared.class: done
    a_closed.class: done
    unlinked.class: done
    freed.class: current
    freed.tooltip: The last handle is closed, so the system frees the memory. On Linux that happens only if the name was unlinked first.
  }
}
```

This is how Python's `SharedMemory.close()` and `SharedMemory.unlink()`
behave,[^shared-memory][^shm-unlink][^create-file-mapping] so the two don't
have to be learned separately.

Because `unlink` removes the name for every process, only one process
should call it, usually the one that created the box. `sharedbox` never
unlinks anything by itself when your program exits, just like
`SharedMemory(track=False)`. On Linux a segment that is never
unlinked stays in `/dev/shm` until reboot, and creating a box under its
name then raises [`SegmentExistsError`][sharedbox.SegmentExistsError].

Leaving a `with` block closes a box, and so does garbage collection. The
docstring of [`close`][sharedbox.SharedBox.close] says what happens in each
case to writes not yet delivered and to forwarding the box started.

## Closing a box while other threads still use it

This section is for the curious and for people working on `sharedbox`
itself; you don't need it to use a box. Within one process, one thread can
call `close` and unmap the memory while another thread is still copying
from it, and a careless design would crash there. The free-threaded build has no global
interpreter lock (GIL),[^pep-703] and Python 3.14 is the first version
where that build is supported rather than experimental.[^pep-779] A normal
build does not prevent the race either: a read or write that waits for
another writer's lock releases the GIL while it waits, so `close` can run
in the meantime.

So each open box has a reader-writer lock that only guards its own
lifetime.[^shared-mutex] Every operation holds it in shared mode;
`close` takes it exclusively, so it waits until running operations finish.

A wait for the write lock (see [Reading and
writing](reading-and-writing.md#the-write-lock-a-sequence-counter)), or for
a copy that no write interrupted, releases the GIL through hooks the
extension registers with `sharedbox.hpp`: `PyEval_SaveThread` before the
first pause and `PyEval_RestoreThread` after the last. Other Python threads
run during the wait, but the waiting thread needs the GIL back before it
can return and give up its shared hold on the lifetime lock. Two orderings
would then deadlock, and the code rules out both:

- `close` holding the GIL while it waits for the exclusive lock. The
  waiting operation could never take the GIL back and finish. `close` is
  bound with `nb::call_guard<nb::gil_scoped_release>`, so it releases the
  GIL before it waits.
- A new call blocking on the lifetime lock while it holds the GIL. The C++
  standard allows a reader-writer lock to make new readers queue behind a
  waiting writer. With such a lock the new call waits for `close`, `close`
  waits for the operation already running, and that operation waits for
  the GIL the new call holds. `enter()` therefore never blocks: it retries
  a non-blocking attempt, and checks `closed` on every pass.

```cpp
std::shared_lock<std::shared_mutex> enter() const {
    std::shared_lock guard(lifetime, std::try_to_lock);
    while (!guard.owns_lock()) {
        check_open();                          // raises BoxClosedError once close() has begun
        std::this_thread::yield();
        guard.try_lock();
    }
    check_open();
    return guard;
}

void Segment::close() {                        // runs without the GIL
    if (impl_->closed.exchange(true))          // enter() now refuses new calls
        return;
    std::unique_lock guard(impl_->lifetime);   // wait for calls already running
    impl_->box = handle();                     // then release the handle
    impl_->writer.reset();                     // and the handle writing() maps
}
```

`close` stores `closed` before it asks for the exclusive lock, so a call
that can't get the shared lock, because `close` holds it or waits for it,
raises [`BoxClosedError`][sharedbox.BoxClosedError] instead of waiting.
The C++ standard allows a reader-writer lock either to queue new readers
behind a waiting writer, the case above, or to let them in first, and on
Linux it lets them in first.[^rwlock] A call that gets in that way after
`closed` is stored still raises `BoxClosedError` at once and lets go, so
new calls hold the lock only long enough for that check, and `close` is
never kept waiting by them.

This lock is only about one box object inside one process. The
[sequence lock](glossary.md#sequence-lock) is what coordinates processes.

A capsule [handle](glossary.md#handle) made by
[`__sharedbox_box__`][sharedbox.SharedBox.__sharedbox_box__] is a separate
mapping of the same [segment](glossary.md#segment), so `close` never waits
for it and never affects it.

## Sources

[^pep-703]:
    PEP 703, "Making the Global Interpreter Lock Optional in CPython".
    <https://peps.python.org/pep-0703/>

[^pep-779]:
    PEP 779, "Criteria for supported status for free-threaded Python".
    <https://peps.python.org/pep-0779/>

[^shared-mutex]:
    cppreference, `std::shared_mutex`.
    <https://en.cppreference.com/w/cpp/thread/shared_mutex>

[^rwlock]:
    Linux manual page `pthread_rwlockattr_setkind_np(3)`: the default kind
    prefers readers.
    <https://man7.org/linux/man-pages/man3/pthread_rwlockattr_setkind_np.3.html>

[^shm-unlink]:
    Linux manual page `shm_unlink(3)`: removing the name, existing mappings
    stay valid.
    <https://man7.org/linux/man-pages/man3/shm_unlink.3.html>

[^create-file-mapping]:
    Microsoft, `CreateFileMappingW`: a mapping backed by the paging file,
    freed when its last handle is closed.
    <https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createfilemappingw>

[^shared-memory]:
    CPython source, `Lib/multiprocessing/shared_memory.py` (the Windows
    branch uses a page-file-backed file mapping), and the documentation of
    `SharedMemory.close()` and `unlink()`.
    <https://github.com/python/cpython/blob/main/Lib/multiprocessing/shared_memory.py>,
    <https://docs.python.org/3/library/multiprocessing.shared_memory.html>
