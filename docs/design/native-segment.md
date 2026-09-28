# Native segment design

This page explains how the C++ part of sharedbox stores a box in shared
memory, and why it is built that way. The byte layout and the protocols
themselves are specified in [segment-layout.md](segment-layout.md); this
page gives the reasons behind them and describes how the Python extension
(`src/sharedbox/_native/`) uses `include/sharedbox/sharedbox.hpp`. Numbers
in brackets, such as [1], point to the sources in the
[References](#references) list at the end.

The Python side decides which fields a box has and where each one lives.
The C++ side owns the shared memory, converts values to and from their
stored bytes, and makes sure a reader never sees a half-finished write.

## 1. One named mapping per box

Every box lives in one block of memory that the operating system lets
several processes map at the same time. The block has a name; any process
that knows the name can open it [1].

On Linux it is a POSIX shared memory object, `/dev/shm/sharedbox.<name>`,
created with mode `0600`, readable and writable by its owner only [1]. On
Windows it is a file mapping backed by the page file, `Local\sharedbox.<name>`,
which Windows deletes when the last process closes it [22]; the standard
library's `multiprocessing.shared_memory` creates the same kind of object
on Windows [2]. Creation always asks for a new name, so a box never
attaches to a block that another program created first.
[segment-layout.md](segment-layout.md#names) lists every object name.

## 2. A fixed layout: header, field table, record

The mapping starts with a 128-byte header, then a table with one entry per
field, one write count per field, the waiter slots, and the record that
holds the field values, 64-byte aligned.
[segment-layout.md](segment-layout.md#layout) gives every offset.

Each field has a fixed place and a fixed size in the record. Fields are
packed by descending alignment (the 8-byte `int` and `float` fields first,
then `str` and `bytes`, then `bool`), not declaration order. The values
passed to `create()` are written into the record before the header's
`magic` word is set, so an attaching process never sees a record before
every field holds its starting value.

Why fixed places instead of something more flexible, such as a dictionary
stored in shared memory:

- Reading or writing a field is one copy of a known number of bytes to or
  from a known address. There is no structure to walk and nothing to
  rebalance.
- Values are stored as plain bytes the native module converts (packing
  numbers the way `struct` would, text as UTF-8). Nothing is ever unpickled.
  Unpickling runs code chosen by whoever wrote the bytes [3], so a box whose
  values were pickles would let any process that can write the block run
  code in every process that reads it.
- A process that opens the block with a different version of the class is
  refused: the `schema_hash` in the header must match the hash the opener
  computes from its own class.

The record's position is stored as an offset rather than as an address,
because each process maps the block at a different address.

### The class fingerprint (`schema_hash`)

The field table tells the C++ side each field's offset, capacity and kind,
but nothing in it says which field is called `position`, or whether the
attaching class declares the field at offset 16 as an `int` or a `float`.
Only the class knows that. So when two processes open the same block,
something has to confirm they agree on the meaning of every byte;
otherwise one process would read another's `float` as an `int` and get
wrong values with no error.

The fingerprint is the first 8 bytes of SHA-256 over the class's identity
and each field's `name:kind:capacity`
([segment-layout.md](segment-layout.md#schema-identity) has the exact text
and a test vector). The identity is `module.qualname` unless the class sets
`identity=`. The attaching process compares the header's fingerprint with
its own class's after the layout checks and before it reads any field.

Anything that changes the meaning of the bytes changes the fingerprint and
makes `attach()` raise `SchemaMismatchError`: a field added, removed,
renamed, reordered or given another type or capacity, and the identity
changed (by default, the class moved to another module or renamed). A
change that leaves the bytes' meaning alone does not: a new method, a
docstring, a default value.

What the fingerprint is not:

- It is not a check of who created the block. Any process that can write the
  block can write any fingerprint into the header. It protects against
  mistakes, such as an old and a new version of a program running at the
  same time, not against a hostile process; section 3 and the `0600`
  permissions deal with that.
- It does not cover changes in how sharedbox itself lays out a record
  between releases. The header's `layout_major` and `layout_minor` do.
- 8 bytes of SHA-256 give 2^64 possible values, so two different classes
  sharing a fingerprint by accident is not a practical concern.

## 3. Never trust the shared copy of the layout

Any process that can open the block can also write to it, including the
header. If the code read a field's offset from the header every time it
copied data, another process could change that offset between the check and
the copy and make this process read or write outside the block. This is the
"time of check, time of use" mistake [4].

So when a process opens an existing block, it copies line 0 of the header
and the field table once, checks the copies against the size of the mapping
as the OS reports it (`fstat` on Linux, `VirtualQuery` on Windows), and
uses only the copies afterwards. The header's own `size` field must match
the mapping size, but the mapping size comes from the OS, not from the
block. The same idea applies to values arriving from Python: `write()`
checks the size of every value before it takes the write lock, so a bad
value in a multi-field update changes nothing.

## 4. The write lock: a sequence counter

Readers should never block writers, and neither should need the operating
system in the common case. A counter in the header provides this. The
technique is known as a sequence lock [5]: the counter is even when no
write is in progress and odd while a write is running.

A writer moves the counter from even to odd with one compare-and-swap,
copies its bytes, and moves it to the next even value. A reader takes no
lock: it notes the counter, copies, and checks that the counter did not
move. If a write happened while the reader was copying, the reader throws
its copy away and tries again [5]. A reader can therefore never return a
mix of old and new bytes, and writes to several fields
(`update(a=..., b=...)`) are seen all at once or not at all.
[segment-layout.md](segment-layout.md#sequence-lock) gives the exact steps
and orderings, including why the unlock is a compare-and-swap rather than
a plain store.

What the `memory_order` arguments and fences are for, in plain terms:
processors and compilers are allowed to reorder memory accesses when a
single thread cannot tell the difference [7]. Here another process can
tell. The fences forbid the reorderings that would break the rule above: the
writer's data copy may not move before it takes the lock or after it
releases it, and the reader's check of the counter may not move before its
copy. This placement of the fences, a release fence after the writer takes
the counter and an acquire fence between the reader's copy and its second
look at the counter, is the one Boehm shows to be correct for C++ [6].

Why not an ordinary mutex:

- A mutex shared between processes needs either the operating system's
  help or a spin loop, and both cost more than one compare-and-swap when
  nobody is competing.
- A reader never makes a writer wait. With a mutex, a slow reader delays
  every writer.
- A process that dies holding a mutex leaves it held in a way other
  processes cannot always detect. A dead writer here leaves an odd counter
  and its pid in `writer_pid`, which `force_unlock()` clears.
- Writers take turns. One lock per box is enough for records the size of a
  dataclass, and it is what lets `update()` change several fields at once.

The counter must be usable from several processes at once. C++ only
guarantees that for atomic operations the processor performs directly,
without a hidden lock inside the program [8]:

```cpp
static_assert(std::atomic_ref<std::uint64_t>::is_always_lock_free);
```

A writer that waits too long, or a reader that keeps losing to writers,
gives up after `lock_timeout` seconds with `LockTimeoutError`, naming the
process id stored in `writer_pid`. That process may have died while
holding the lock; `force_unlock()` releases it.

The wait for the lock spins, then yields, then sleeps for a doubling
interval of at most 1 ms. On Windows a sleep ends on a timer tick, so a
wait is bounded below by the timer granularity: on a GitHub Actions
Windows runner a 1 ms `lock_timeout` ended after 15.7 ms (median of 200
waits), against 1.2 ms on a GitHub Actions Linux runner. A lock holder
that is descheduled for one timer tick can also make a short timeout
expire: on the same Windows runner, with a 10 ms `lock_timeout`, 1 of
4.3 million writes by two contending writers timed out.

## 5. Waking a process that waits for a change

`box.events` and `box.watch()` need a way to sleep until another process
writes. Checking in a loop would waste CPU; sleeping for a fixed time would
add delay. Each waiting thread therefore holds a waiter slot in the
segment, and a write wakes the slots that are occupied.

- One slot per waiting watcher. A box has `max_waiters` slots (64 by
  default, 1 to 4096), shared by every process. A slot records its owner's
  pid, start time and pid namespace.
- Linux wakes through one futex word in the header [9]: a waiter sleeps
  with `FUTEX_WAIT` while `wake_word` still holds the value it read, and a
  writer increments the word and calls `FUTEX_WAKE` once for everybody.
  Windows has `WaitOnAddress`, but it only works between threads of one
  process [10], so each slot gets its own auto-reset event,
  `Local\sharedbox.<name>.w<i>` [11], and a writer sets the event of every
  occupied slot. On both systems a write to a box nobody waits on makes no
  system call to wake anyone: the writer reads `waiters` and stops at 0.
- A process killed while it holds a slot does not keep it. The next
  register or attach in any process checks each occupied slot's owner with
  the liveness rules of section 9 and frees the slots of dead processes,
  except slots recorded in another pid namespace, whose pids cannot be
  checked from here.
- The Python watcher waits in steps of at most 1 s. The step is not a poll
  for changes: writes and `interrupt()` wake it at once. After each step
  the watcher checks that its slot still records its own pid, start time
  and namespace, and claims a new slot if not. While every slot is taken,
  the watcher logs one warning to the `sharedbox` logger and checks for
  changes once a second until a slot is free.
- `close()` interrupts its own watcher: it sets the interrupt flag of the
  watcher's slot and wakes that slot, so the watcher thread returns at once
  instead of at the end of its step.

A wake-up is never lost. On Linux the waiter reads `wake_word` before it
reads the generation, and the writer changes the generation before
`wake_word`; if a write lands between the waiter's two reads, `wake_word`
no longer matches and the wait returns at once [9]. On Windows each event
belongs to one slot, so one waiter cannot take the wake-up meant for
another.

[segment-layout.md](segment-layout.md#waiter-slots) gives the order of the
stores that claim, free and release a slot, and the few steps at which a
killed process leaves a slot stuck or the count one too high.

## 6. Closing a box while other threads still use it

One thread can call `close()` and unmap the memory while another thread of
the same process is copying from it. The free-threaded build has no global
interpreter lock (GIL) [12], and Python 3.14 is the first version where that
build is supported rather than experimental [17]. A normal build does not
prevent the race either: a read or write that waits for another writer's
lock releases the GIL while it waits, so `close()` can run in the meantime.

Each open box therefore has a reader-writer lock that only protects its own
lifetime [13]. Every operation holds it in shared mode; `close()` takes it
exclusively, so it waits until running operations finish.

A wait for the write lock (section 4), or for a copy that no write
interrupted, releases the GIL through hooks the extension registers with
`sharedbox.hpp`: `PyEval_SaveThread` before the first pause and
`PyEval_RestoreThread` after the last. Other Python threads run during the
wait, but the waiting thread needs the GIL back before it can return and
give up its shared hold on the lifetime lock. Two orderings would then
deadlock, and the code rules out both:

- `close()` holding the GIL while it waits for the exclusive lock. The
  waiting operation could never take the GIL back and finish. `close()` is
  bound with `nb::call_guard<nb::gil_scoped_release>`, so it releases the GIL
  before it waits.
- A new call blocking on the lifetime lock while it holds the GIL. The C++
  standard allows a reader-writer lock to make new readers queue behind a
  waiting writer; with such a lock the new call waits for `close()`,
  `close()` waits for the operation already running, and that operation
  waits for the GIL the new call holds. `enter()` therefore never blocks: it
  retries a non-blocking attempt, and checks `closed` on every pass.

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
}
```

`close()` stores `closed` before it asks for the exclusive lock. A call that
cannot get the shared lock, because `close()` holds it or waits for it,
raises `BoxClosedError` instead of waiting. On Linux the standard
reader-writer lock lets new readers in ahead of a waiting writer [14]; a
call that gets in after `closed` is stored raises `BoxClosedError` at once
and lets go, so new calls hold the lock only for that check.

This lock is only about one box object inside one process. The sequence
counter from section 4 is what coordinates processes.

A capsule handle made by `__sharedbox_box__` is a separate mapping of the
same segment, so `close()` never waits for it and never affects it.

## 7. Lifetime: the same rules as `multiprocessing.shared_memory`

`close()` only detaches this process. `unlink()` removes the name: on Linux
it calls `shm_unlink`, which works like deleting an open file: processes
that already have the block keep using it, and nobody new can open it
[15]. On Windows there is nothing to remove; Windows frees the block when
its last user closes it [22]. This is how Python's `SharedMemory.close()`
and `SharedMemory.unlink()` behave [2], so the two do not have to be
learned separately.

## 8. How the module is compiled

The module is built with nanobind, which connects C++ to Python, as C++20
against `sharedbox::headers`.

```cmake
nanobind_add_module(_native
    STABLE_ABI
    FREE_THREADED
    LTO
    NB_DOMAIN sharedbox
    src/sharedbox/_native/codec.cpp
    src/sharedbox/_native/module.cpp
    src/sharedbox/_native/segment.cpp)
target_link_libraries(_native PRIVATE sharedbox::headers)
```

`STABLE_ABI` asks for a module that works on every later Python version
without recompiling; nanobind supports this from Python 3.12 [16], so one
wheel covers 3.12, 3.13, 3.14 and later. `FREE_THREADED` asks for a module
that runs without the GIL; it only takes effect on free-threaded Python
[18]. nanobind ignores whichever option does not fit the interpreter doing
the build [16] [18], which is why one line produces all three wheels:

| Wheel | Built on | Runs on |
| --- | --- | --- |
| `cp311-cp311` | CPython 3.11 | 3.11 only (the stable ABI needs 3.12) |
| `cp312-abi3` | CPython 3.12 | every normal CPython from 3.12 |
| `cp314-cp314t` | free-threaded CPython 3.14 | free-threaded 3.14 |

Free-threaded Python cannot load stable-ABI modules [18]; a stable ABI for
free-threaded builds (`abi3t`) starts with Python 3.15 [19].

The build also installs `include/sharedbox/` and the CMake config into the
wheel, so other extensions can compile against the same header
(see [library-authors.md](../library-authors.md)).

## 9. Checking whether a process is still running

A process id alone does not identify a process: once a process exits, the
operating system can give its pid to a new one. `sharedbox.hpp` therefore
names a process by its pid and its start time, the way psutil tells a
process from a later one with the same pid [20]. On Windows the start time
is the creation time from `GetProcessTimes`. On Linux it is field 22 of
`/proc/<pid>/stat` [21]. `process_alive()` reads the start time of whatever
process has the pid now and decides:

- No process has the pid: dead.
- A process has the pid but started at another time: dead, because the pid
  was reused.
- A process has the pid but its start time cannot be read, because it
  belongs to another user or `/proc` is mounted with `hidepid`: alive. The
  process exists, and nothing shows it is a different one. The same holds
  when the recorded start time is the one that could not be read.
- On Linux, a zombie (state `Z` or `X` in `/proc/<pid>/stat`) is dead. It
  has exited and keeps its `/proc` entry only until its parent reaps
  it [21].

On Linux a pid means something only inside its pid namespace, so slots and
the creator fields also record the namespace; a process in another
namespace, or one whose namespace is unknown, is never judged dead.

The check is used in two places:

- Freeing the waiter slots of dead processes (section 5).
- Naming the creator in `SegmentExistsError`. When a create finds the name
  taken, the extension reads the existing header with
  `sharedbox::inspect()` and says whether its creator still runs (with its
  pid), runs in another pid namespace, or has exited, in which case the
  segment is probably left over from a crash and `Box.unlink(name)`
  removes it. A name that holds no published box gets a message saying it
  may be left over from a crash during create. Nothing is removed
  automatically: other processes may still use a segment whose creator
  died.

## References

1. Linux manual page `shm_open(3)`: named shared memory, the name as the
   way to open it, permission bits.
   https://man7.org/linux/man-pages/man3/shm_open.3.html
2. CPython source, `Lib/multiprocessing/shared_memory.py` (Windows branch
   uses a page-file-backed file mapping), and the documentation of
   `SharedMemory.close()` and `unlink()`.
   https://github.com/python/cpython/blob/main/Lib/multiprocessing/shared_memory.py
   https://docs.python.org/3/library/multiprocessing.shared_memory.html
3. Python documentation, `pickle`, the warning at the top of the page:
   unpickling can execute arbitrary code.
   https://docs.python.org/3/library/pickle.html
4. MITRE CWE-367, "Time-of-check Time-of-use (TOCTOU) Race Condition".
   https://cwe.mitre.org/data/definitions/367.html
5. Linux kernel documentation, "Sequence counters and sequential locks":
   the even/odd counter, readers that retry.
   https://www.kernel.org/doc/html/latest/locking/seqlock.html
6. Hans-J. Boehm, "Can Seqlocks Get Along With Programming Language Memory
   Models?", HP Laboratories technical report HPL-2012-68, 2012: which
   fences make a sequence lock correct in C++.
   https://www.hpl.hp.com/techreports/2012/HPL-2012-68.pdf
7. cppreference, `std::memory_order`: what the compiler and processor may
   reorder, and what acquire and release forbid.
   https://en.cppreference.com/w/cpp/atomic/memory_order
8. cppreference, `std::atomic_ref<T>::is_always_lock_free`.
   https://en.cppreference.com/w/cpp/atomic/atomic_ref/is_always_lock_free
9. Linux manual page `futex(2)`: `FUTEX_WAIT` sleeps only while the value
   still equals the expected one; shared (non-private) futexes work across
   processes.
   https://man7.org/linux/man-pages/man2/futex.2.html
10. Microsoft, `WaitOnAddress`: works between threads of the same process.
    https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitonaddress
11. Microsoft, `CreateEventW`: named events shared between processes,
    auto-reset events.
    https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createeventw
12. PEP 703, "Making the Global Interpreter Lock Optional in CPython".
    https://peps.python.org/pep-0703/
13. cppreference, `std::shared_mutex`.
    https://en.cppreference.com/w/cpp/thread/shared_mutex
14. Linux manual page `pthread_rwlockattr_setkind_np(3)`: the default kind
    prefers readers.
    https://man7.org/linux/man-pages/man3/pthread_rwlockattr_setkind_np.3.html
15. Linux manual page `shm_unlink(3)`: removing the name, existing mappings
    stay valid.
    https://man7.org/linux/man-pages/man3/shm_unlink.3.html
16. nanobind, CMake interface: `STABLE_ABI` (CPython 3.12 or newer),
    `NB_DOMAIN`.
    https://nanobind.readthedocs.io/en/latest/api_cmake.html
17. PEP 779, "Criteria for supported status for free-threaded Python".
    https://peps.python.org/pep-0779/
18. nanobind, "Free-threaded Python": `FREE_THREADED`, ignored on builds
    that do not support it; no stable ABI for free-threaded linked builds.
    https://nanobind.readthedocs.io/en/latest/free_threaded.html
19. PEP 803, the stable ABI for free-threaded builds (`abi3t`).
    https://peps.python.org/pep-0803/
20. psutil (BSD-3-Clause), `Process`: a process is identified by its pid
    and its creation time, so a reused pid is not mistaken for the same
    process.
    https://github.com/giampaolo/psutil
21. Linux manual page `proc_pid_stat(5)`: the process state (field 3) and
    `starttime` (field 22).
    https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html
22. Microsoft, `CreateFileMappingW`: a mapping backed by the paging file,
    freed when its last handle is closed.
    https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createfilemappingw
