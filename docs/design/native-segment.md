# Native segment design

This page explains how the C++ part of sharedbox (`src/sharedbox/_native/`)
stores a box in shared memory, and why it is built that way. Each section
states a choice and shows the code that implements it. Numbers in brackets,
such as [1], point to the sources in the [References](#references) list at
the end, where each idea can be checked.

The Python side decides which fields a box has and turns values into bytes.
The C++ side only moves bytes: it owns the shared memory, knows where each
field lives, and makes sure a reader never sees a half-finished write.

## 1. One named block of shared memory per box

Every box lives in one block of memory that the operating system lets
several processes map at the same time. The block has a name; any process
that knows the name can open it [3].

```cpp
#ifdef _WIN32
using Managed = bipc::managed_windows_shared_memory;
#else
using Managed = bipc::managed_shared_memory;
#endif
```

Boost.Interprocess provides both types. The difference matters on Windows.
Boost's portable type, `managed_shared_memory`, is not real shared memory
there: Boost imitates it with an ordinary file under
`C:\ProgramData\boost_interprocess`, so every access can involve the file
system [1]. `managed_windows_shared_memory` asks Windows for a native shared
memory object backed by the page file, and Windows deletes that object when
the last process closes it [1]. The standard library's
`multiprocessing.shared_memory` creates the same kind of object on
Windows [4].

The "managed" flavour is used even though a box today is one fixed block of
bytes. A managed segment comes with a memory allocator and named objects [2],
and later versions need both for fields whose size can change (text without
a fixed capacity, lists). Switching the segment type later would change the
layout in memory.

On Linux the block is created readable and writable by its owner only [3]:

```cpp
bipc::permissions perms;
perms.set_permissions(0600);   // owner read and write, nobody else
```

and creation always asks for a new name, so a box never attaches to a block
that some other program created first [2]:

```cpp
impl->segment = Managed(bipc::create_only, name.c_str(), size, nullptr, perms);
```

## 2. A fixed layout: header, field table, record

Inside the block there are three things: a fixed 64-byte header, a table
with one entry per field, and the record that holds the field values.

```cpp
struct alignas(64) Header {                    // offset
    std::atomic<std::uint64_t> magic;          //  0  written last; marks the header as complete
    std::uint32_t abi_version;                 //  8  layout version, 3
    std::uint32_t field_count;                 // 12
    std::uint64_t schema_hash;                 // 16  fingerprint of the Python class
    std::uint32_t record_size;                 // 24
    std::uint32_t record;                      // 28  where the record starts, as an offset
    std::uint32_t tail;                        // 32  where the field table starts, as an offset
    std::atomic<std::uint32_t> writer_pid;     // 36  who holds the write lock
    std::atomic<std::uint64_t> seq;            // 40  the write lock, see section 4
    std::atomic<std::uint64_t> generation;     // 48  counts every write
    std::atomic<std::uint32_t> wake_word;      // 56  used to wake waiters, see section 5
    std::atomic<std::uint32_t> waiters;        // 60
};

struct StoredField {                           // one entry of the field table
    std::uint32_t offset;
    std::uint32_t capacity_and_kind;           // low 24 bits: capacity; top 8: the field's kind code
};
```

The header fills one 64-byte cache line exactly, with no padding. The
members up to `tail` are written when the block is created and read only
while a process attaches. The members from `writer_pid` on change on every
write. Keeping both groups in one line means a write changes one line of
the header rather than two, and no process reads the first group often
enough for the writes to slow it down. `magic` and `abi_version` sit at the
same offsets as in layout version 1, so a process can open a block of an
older version and report which version it is.

The field table, which the code calls the tail, holds `field_count` entries
of 8 bytes, followed by one 8-byte write counter per field. Each entry packs
a 32-bit offset and a 32-bit `capacity_and_kind`: the low 24 bits hold the
capacity (at most 1 MiB, which needs 21 bits) and the top 8 hold the field's
kind code (`bool` 0, `int` 1, `float` 2, `str` 3, `bytes` 4), so the entry
stays 8 bytes with no byte of its own set aside for the kind. The table and
the record share one allocation, with the record moved forward to the next
64-byte boundary. Boost's aligned allocation temporarily asks for twice the
requested size, which would make a box with a 1 MiB field need a block of
over 2 MiB.

The block is as large as these parts plus 1024 bytes for Boost's own
bookkeeping, which needs at most 552 bytes on Windows and on Linux with glibc, rounded
up to 4 KiB. A box with three small fields takes 4 KiB; one with 256
integer fields takes 8 KiB. `static_assert`s on `sizeof` and `offsetof` in
`segment.cpp` stop the build if any of these layouts changes by accident.

Each field has a fixed place and a fixed size in the record. A number takes
8 bytes. Text and raw bytes take a 4-byte length followed by up to
`capacity` bytes:

```text
record: | count (8) | ratio (8) | label: length (4) + up to 32 bytes, padded to 8 |
```

Fields are packed by descending alignment (the 8-byte `int` and `float`
fields first, then `str` and `bytes`, then `bool`), not declaration order,
so a field's place in the record need not match its place in the class.

The values passed to `create()` are written into the record before the
header's `magic` word is set, so an attaching process never sees a record
before every field holds its starting value.

Why fixed places instead of something more flexible, such as a dictionary
stored in shared memory:

- Reading or writing a field is one copy of a known number of bytes to or
  from a known address. There is no structure to walk and nothing to
  rebalance.
- Values are stored as plain bytes the native module converts (packing
  numbers the way `struct` would, text as UTF-8). Nothing is ever unpickled.
  Unpickling runs code chosen by whoever wrote the bytes [5], so a box whose
  values were pickles would let any process that can write the block run
  code in every process that reads it.
- A process that opens the block with a different version of the class is
  refused: the `schema_hash` in the header must match the hash the opener
  computes from its own class.

### The class fingerprint (`schema_hash`)

The C++ side has no idea what the bytes in the record mean. It knows each
field's offset, capacity and whether it has a length prefix, but not whether
8 bytes at offset 16 are an `int` or a `float`, or which field is called
`position`. Only the Python class knows that. So when two processes open the
same block, something has to confirm they agree on the meaning of every byte;
otherwise one process would read another's `float` as an `int`, or the field
it calls `speed` at the offset where the other writes `position`, and get
wrong values with no error.

The fingerprint is how they agree. The Python side computes it from the
class when the class is defined (`build_layout` in `_layout.py`):

```python
identity = "|".join(
    [class_identity(cls), *(f"{s.name}:{s.kind}:{s.capacity}" for s in specs)]
)
schema_hash = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "little")
```

For a class `Motor` in module `robot` with fields `position: int`,
`enabled: bool` and `label: Annotated[str, Capacity(32)]`, the text that is
hashed is:

```text
robot.Motor|position:int:8|enabled:bool:1|label:str:32
```

- `class_identity` is `module.qualname`. `__mp_main__` is replaced by
  `__main__`, because multiprocessing's spawn start method re-imports the
  main script under that name, and the same class must give the same
  fingerprint in the parent and in a spawned child.
- Each field adds its name, its kind (`bool`, `int`, `float`, `str`,
  `bytes`) and its capacity in bytes (1 for `bool`, 8 for `int` and `float`,
  the `Capacity` for `str` and `bytes`), in declaration order with base class
  fields first.
- The first 8 bytes of the SHA-256 digest, read as a little-endian unsigned
  64-bit integer, are the fingerprint.

Offsets are not part of the text: they follow from the kinds, capacities and
order, so hashing those covers them.

The creating process stores the fingerprint in the header. A process that
attaches passes the fingerprint of its own class, and the C++ side compares
the two, right after checking `abi_version` and before it looks at the field
table or the record:

```cpp
if (h->schema_hash != schema_hash)
    throw SchemaMismatch("segment '" + name + "' was created by a different class");
```

Anything that changes the meaning of the bytes changes the fingerprint and
makes `attach()` raise `SchemaMismatchError`: a field added, removed,
renamed, reordered or given another type or capacity, and the class moved to
another module or renamed. A change that leaves the bytes' meaning alone
does not: a new method, a docstring, a default value.

What the fingerprint is not:

- It is not a check of who created the block. Any process that can write the
  block can write any fingerprint into the header. It protects against
  mistakes, such as an old and a new version of a program running at the
  same time, not against a hostile process; section 3 and the `0600`
  permissions deal with that.
- It does not cover changes in how sharedbox itself lays out a record
  between releases. The header's `abi_version` does.
- 8 bytes of SHA-256 give 2^64 possible values, so two different classes
  sharing a fingerprint by accident is not a practical concern.

The record's position is stored as an offset rather than as an address,
because each process maps the block at a different address [6]. Boost's
`get_handle_from_address` and `get_address_from_handle` convert between the
two [2].

## 3. Never trust the shared copy of the layout

Any process that can open the block can also write to it, including the
header. If the code read a field's offset from the header every time it
copied data, another process could change that offset between the check and
the copy and make this process read or write outside the block. This is the
"time of check, time of use" mistake [7].

So when a process opens an existing block, it checks every field once and
keeps its own copy:

```cpp
for (std::uint32_t i = 0; i < count; ++i) {
    StoredField stored;
    std::memcpy(&stored, tail + i * sizeof(StoredField), sizeof stored);  // copy first
    const FieldDesc f{stored.offset, stored.capacity_and_kind & ~kPrefixed, ...};
    if (!field_fits(f, record_size))           // then check the copy
        throw SchemaMismatch("segment '" + name + "' has a corrupt header");
    impl->fields.push_back(f);                 // only this copy is used afterwards
}
```

Every later copy of field data uses `impl->fields`, never the shared table.
The offsets of the record and of the table, the record size and the field
count are each read once into a local variable in the same way. Each one is
checked against the size of the block before anything is read through it,
and the checked value is the one used.

The same idea applies to values arriving from Python: `write()` checks the
size of every value before it takes the write lock, so a bad value in a
multi-field update changes nothing.

There is one limit. The total size of the block comes from Boost's own
bookkeeping, which also lives inside the block [8]. Boost trusts that
bookkeeping for its own lookups, so the check here catches accidental
corruption but not a deliberately forged header. Only a process that can
already write the block could forge one; on Linux that means a process of
the same user, because of the `0600` permissions [3].

## 4. The write lock: a sequence counter

Readers should never block writers, and neither should need the operating
system in the common case. A counter in the header provides this. The
technique is known as a sequence lock [9].

The rule: the counter is even when no write is in progress and odd while a
write is running [9].

A writer:

```cpp
// 1. Take the lock: move the counter from even to odd in one indivisible step.
std::uint64_t seq = header->seq.load(std::memory_order_relaxed);
if ((seq & 1) == 0 &&
    header->seq.compare_exchange_weak(seq, seq + 1, std::memory_order_acquire)) {
    std::atomic_thread_fence(std::memory_order_release);
    // 2. Copy the new bytes into the record.
    // 3. Release the lock: counter back to even.
    header->seq.fetch_add(1, std::memory_order_release);
}
```

A reader does not take any lock. It notes the counter, copies, and checks
that the counter did not move:

```cpp
for (;;) {
    std::uint64_t before = header->seq.load(std::memory_order_acquire);
    if ((before & 1) == 0) {                  // no write running right now
        copy();                                // may overlap a write that starts now
        std::atomic_thread_fence(std::memory_order_acquire);
        if (header->seq.load(std::memory_order_relaxed) == before)
            return;                            // nothing changed while copying: done
    }
    // a write was running or started: try again
}
```

If a write happened while the reader was copying, the counter changed and
the reader throws its copy away and tries again [9]. A reader can therefore
never return a mix of old and new bytes, and writes to several fields
(`update(a=..., b=...)`) are seen all at once or not at all.

What the `memory_order` arguments and fences are for, in plain terms:
processors and compilers are allowed to reorder memory accesses when a
single thread cannot tell the difference [11]. Here another process can
tell. The fences forbid the reorderings that would break the rule above: the
writer's data copy may not move before it takes the lock or after it
releases it, and the reader's check of the counter may not move before its
copy. This placement of the fences, a release fence after the writer takes
the counter and an acquire fence between the reader's copy and its second
look at the counter, is the one Boehm shows to be correct for C++ [10].

Why not an ordinary mutex:

- Boost's `interprocess_mutex` on Windows is, by default, a loop that polls
  a value in memory; its native alternative goes through named Windows
  kernel objects [13]. Both cost more than a counter when nobody is
  competing.
- A reader never makes a writer wait. With a mutex, a slow reader delays
  every writer.
- Writers take turns. One lock per box is enough for records the size of a
  dataclass, and it is what lets `update()` change several fields at once.

The counter must be usable from several processes at once. C++ only
guarantees that for atomic variables the processor updates directly, without
a hidden lock inside the program [12]:

```cpp
static_assert(std::atomic<std::uint64_t>::is_always_lock_free);
```

A writer that waits too long, or a reader that keeps losing to writers,
gives up after `lock_timeout` seconds with `LockTimeoutError`, naming the
process id stored in `writer_pid`. That process may have died while
holding the lock; `force_unlock()` clears it.

## 5. Waking a process that waits for a change

`box.events` and `box.watch()` need a way to sleep until another process
writes. Checking in a loop would waste CPU; sleeping for a fixed time would
add delay. Each write therefore increments `generation` and `wake_word`, and
wakes whoever waits on `wake_word`.

On Linux the kernel can put threads to sleep on a 32-bit value in shared
memory and wake them from any process (the futex system call) [14].
Waiters register in the `waiters` counter while they sleep, and a writer
makes the wake call only when the counter is not zero:

```cpp
// waiter: sleep only while wake_word still equals the value it saw
waiters.fetch_add(1);
syscall(SYS_futex, &wake_word, FUTEX_WAIT, expected, &timeout, nullptr, 0);
waiters.fetch_sub(1);

// writer, after each write
wake_word.fetch_add(1);
if (waiters.load() != 0)
    syscall(SYS_futex, &wake_word, FUTEX_WAKE, INT_MAX, nullptr, nullptr, 0);
```

A waiter that registers after the writer read `waiters` sees the new
`wake_word`, so FUTEX_WAIT returns at once. A process killed while it
waits leaves the counter one too high for as long as the segment exists;
writers then make the wake call on every write, as if someone waited, and
no wake-up is lost.

Windows has a similar call, `WaitOnAddress`, but it only works between
threads of one process, not across processes [15]. So on Windows each box
gets a named semaphore, `Local\sharedbox.<name>.wake` [16]. Waiters register
in `waiters` the same way, and a writer releases the semaphore once per
registered waiter:

```cpp
// writer
wake_word.fetch_add(1);
LONG pending = static_cast<LONG>(waiters.load());
if (pending > 0)
    ReleaseSemaphore(semaphore, pending, nullptr);
```

On both systems a write to a box nobody watches makes no system call to
wake anyone.

On Windows a waiter killed while it waits costs more than on Linux. The
counter stays one too high, so every later write releases a permit that no
waiter takes. The permits add up, to at most `LONG_MAX`, the semaphore's
maximum, and every later wait on that box then returns at once instead of
sleeping, so a waiting thread keeps checking `generation` in a loop and uses
CPU until the permits are used up. No wake-up is lost. Removing a dead
waiter's count needs a check of whether a process is still running, which
the segment does not have yet.

On Linux a wake-up can never be lost: the waiter reads `wake_word` before it
reads `generation`, and the writer changes `generation` before `wake_word`.
If a write lands between the waiter's two reads, `wake_word` no longer
matches and the wait returns at once instead of sleeping [14]. On Windows,
when several processes or threads wait on the same box, one waiter can take
the permit released for another, so each Windows wait sleeps for at most
50 ms before checking `wake_word` and `generation` again, which bounds how
late a missed wake-up can arrive [16].

The code for this is in `notifier.hpp` and `notifier.cpp`.

## 6. Closing a box while other threads still use it

On a normal Python build, the global interpreter lock (GIL) keeps two
Python threads from running C++ code of this module at the same time. The
free-threaded build has no GIL [17], and Python 3.14 is the first version
where that build is supported rather than experimental [22]. Without the
GIL, one thread could call `close()` and unmap the memory while another
thread is copying from it.

Each open box therefore has a reader-writer lock that only protects its own
lifetime [18]. Every operation takes it in shared mode; `close()` takes it
exclusively, so it waits until running operations finish:

```cpp
std::string Segment::read(std::uint32_t index) const {
    std::shared_lock guard(impl_->lifetime);   // many operations at once are fine
    impl_->check_open();                        // raises BoxClosedError after close()
    ...
}

void Segment::close() {
    if (impl_->closed.exchange(true))           // new calls now fail check_open()
        return;
    std::unique_lock guard(impl_->lifetime);   // wait for calls already running
    ...                                         // then unmap
}
```

`close()` marks the box as closed before it waits. On Linux, the standard
reader-writer lock lets new readers in ahead of a waiting writer [19], so if
`close()` waited first, a stream of reads could keep it waiting forever.
Marking first turns those new reads away with `BoxClosedError`.

This lock is only about one box object inside one process. The sequence
counter from section 4 is what coordinates processes.

## 7. Lifetime: the same rules as `multiprocessing.shared_memory`

`close()` only detaches this process. `unlink()` removes the name.

```cpp
void Segment::unlink(const std::string &name) {
#ifndef _WIN32
    if (!bipc::shared_memory_object::remove(name.c_str()))
        throw SegmentMissing("no segment named '" + name + "'");
#endif
}
```

On Linux, removing the name works like deleting an open file: processes that
already have the block keep using it, and nobody new can open it [20]. On
Windows there is nothing to remove; Windows frees the block when its last
user closes it [1]. This is how Python's `SharedMemory.close()` and
`SharedMemory.unlink()` behave [4], so the two do not have to be learned
separately.

## 8. How the module is compiled

The module is built with nanobind, which connects C++ to Python.

```cmake
nanobind_add_module(_native
    STABLE_ABI
    FREE_THREADED
    NB_DOMAIN sharedbox
    src/sharedbox/_native/module.cpp
    src/sharedbox/_native/segment.cpp)
```

`STABLE_ABI` asks for a module that works on every later Python version
without recompiling; nanobind supports this from Python 3.12 [21], so one
wheel covers 3.12, 3.13, 3.14 and later. `FREE_THREADED` asks for a module
that runs without the GIL; it only takes effect on free-threaded Python
[23]. nanobind ignores whichever option does not fit the interpreter doing
the build [21] [23], which is why one line produces all three wheels:

| Wheel | Built on | Runs on |
| --- | --- | --- |
| `cp311-cp311` | CPython 3.11 | 3.11 only (the stable ABI needs 3.12) |
| `cp312-abi3` | CPython 3.12 | every normal CPython from 3.12 |
| `cp314-cp314t` | free-threaded CPython 3.14 | free-threaded 3.14 |

Free-threaded Python cannot load stable-ABI modules [23]; a stable ABI for
free-threaded builds (`abi3t`) starts with Python 3.15 [24].

## References

1. Boost.Interprocess, "Sharing memory between processes": emulated shared
   memory on Windows, `windows_shared_memory`, its lifetime.
   https://www.boost.org/doc/libs/latest/doc/html/interprocess/sharedmemorybetweenprocesses.html
2. Boost.Interprocess, "Managed Memory Segments": named objects, the
   allocator, `create_only`, handles and addresses.
   https://www.boost.org/doc/libs/latest/doc/html/interprocess/managed_memory_segments.html
3. Linux manual page `shm_open(3)`: named shared memory, the name as the
   way to open it, permission bits.
   https://man7.org/linux/man-pages/man3/shm_open.3.html
4. CPython source, `Lib/multiprocessing/shared_memory.py` (Windows branch
   uses a page-file-backed file mapping), and the documentation of
   `SharedMemory.close()` and `unlink()`.
   https://github.com/python/cpython/blob/main/Lib/multiprocessing/shared_memory.py
   https://docs.python.org/3/library/multiprocessing.shared_memory.html
5. Python documentation, `pickle`, the warning at the top of the page:
   unpickling can execute arbitrary code.
   https://docs.python.org/3/library/pickle.html
6. Boost.Interprocess, "Mapping Address Independent Pointer: offset_ptr":
   why an address cannot be stored in shared memory.
   https://www.boost.org/doc/libs/latest/doc/html/interprocess/offset_ptr.html
7. MITRE CWE-367, "Time-of-check Time-of-use (TOCTOU) Race Condition".
   https://cwe.mitre.org/data/definitions/367.html
8. Boost.Interprocess source, `detail/managed_memory_impl.hpp`: `get_size()`
   returns a value read from the segment's own header.
   https://github.com/boostorg/interprocess/blob/develop/include/boost/interprocess/detail/managed_memory_impl.hpp
9. Linux kernel documentation, "Sequence counters and sequential locks":
   the even/odd counter, readers that retry.
   https://www.kernel.org/doc/html/latest/locking/seqlock.html
10. Hans-J. Boehm, "Can Seqlocks Get Along With Programming Language Memory
    Models?", HP Laboratories technical report HPL-2012-68, 2012: which
    fences make a sequence lock correct in C++.
    https://www.hpl.hp.com/techreports/2012/HPL-2012-68.pdf
11. cppreference, `std::memory_order`: what the compiler and processor may
    reorder, and what acquire and release forbid.
    https://en.cppreference.com/w/cpp/atomic/memory_order
12. cppreference, `std::atomic<T>::is_always_lock_free`.
    https://en.cppreference.com/w/cpp/atomic/atomic/is_always_lock_free
13. Boost.Interprocess, "Acknowledgements, notes and links": how its
    process-shared mutexes are built on Windows.
    https://www.boost.org/doc/libs/latest/doc/html/interprocess/acknowledgements_notes.html
14. Linux manual page `futex(2)`: `FUTEX_WAIT` sleeps only while the value
    still equals the expected one; shared (non-private) futexes work across
    processes.
    https://man7.org/linux/man-pages/man2/futex.2.html
15. Microsoft, `WaitOnAddress`: works between threads of the same process.
    https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitonaddress
16. Microsoft, `CreateSemaphoreW`: named semaphores shared between
    processes, `Local\` names.
    https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createsemaphorew
17. PEP 703, "Making the Global Interpreter Lock Optional in CPython".
    https://peps.python.org/pep-0703/
18. cppreference, `std::shared_mutex`.
    https://en.cppreference.com/w/cpp/thread/shared_mutex
19. Linux manual page `pthread_rwlockattr_setkind_np(3)`: the default kind
    prefers readers.
    https://man7.org/linux/man-pages/man3/pthread_rwlockattr_setkind_np.3.html
20. Linux manual page `shm_unlink(3)`: removing the name, existing mappings
    stay valid.
    https://man7.org/linux/man-pages/man3/shm_unlink.3.html
21. nanobind, CMake interface: `STABLE_ABI` (CPython 3.12 or newer),
    `NB_DOMAIN`.
    https://nanobind.readthedocs.io/en/latest/api_cmake.html
22. PEP 779, "Criteria for supported status for free-threaded Python".
    https://peps.python.org/pep-0779/
23. nanobind, "Free-threaded Python": `FREE_THREADED`, ignored on builds
    that do not support it; no stable ABI for free-threaded linked builds.
    https://nanobind.readthedocs.io/en/latest/free_threaded.html
24. PEP 803, the stable ABI for free-threaded builds (`abi3t`).
    https://peps.python.org/pep-0803/
