# Native segment design

This page explains how the C++ part of sharedbox (`src/sharedbox/_native/`)
stores a box in shared memory, and why it is built that way. Each section
states a choice, shows the code that implements it, and lists sources you
can use to check the reasoning.

The Python side decides which fields a box has and turns values into bytes.
The C++ side only moves bytes: it owns the shared memory, knows where each
field lives, and makes sure a reader never sees a half-finished write.

## 1. One named block of shared memory per box

Every box lives in one block of memory that the operating system lets
several processes map at the same time. The block has a name; any process
that knows the name can open it.

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
system. `managed_windows_shared_memory` asks Windows for a native shared
memory object backed by the page file, which is what Windows programs
normally use and what the standard library's `multiprocessing.shared_memory`
uses too. Windows deletes that object when the last process closes it.

The "managed" flavour is used even though a box today is one fixed block of
bytes. A managed segment comes with a memory allocator and named objects, and
later versions need both for fields whose size can change (text without a
fixed capacity, lists). Switching the segment type later would change the
layout in memory.

On Linux the block is created readable and writable by its owner only:

```cpp
bipc::permissions perms;
perms.set_permissions(0600);   // owner read and write, nobody else
```

and creation always asks for a new name, so a box never attaches to a block
that some other program created first:

```cpp
impl->segment = Managed(bipc::create_only, name.c_str(), size, nullptr, perms);
```

Sources:
- Boost.Interprocess, "Sharing memory between processes" (emulation on
  Windows, `windows_shared_memory`, lifetime):
  https://www.boost.org/doc/libs/latest/doc/html/interprocess/sharedmemorybetweenprocesses.html
- Boost.Interprocess, "Managed Memory Segments":
  https://www.boost.org/doc/libs/latest/doc/html/interprocess/managed_memory_segments.html
- `shm_open(3)` on Linux, for how named shared memory and its permissions
  work: https://man7.org/linux/man-pages/man3/shm_open.3.html

## 2. A fixed layout: header, field table, record

Inside the block there are two things: a header with bookkeeping, and the
record that holds the field values.

```cpp
struct Header {
    std::atomic<std::uint64_t> magic;          // written last; marks the header as complete
    std::uint32_t abi_version;                 // layout version of this header
    std::uint32_t field_count;
    std::uint64_t schema_hash;                 // fingerprint of the Python class
    std::uint64_t record_size;
    Managed::handle_t record;                  // where the record starts, as an offset
    std::atomic<std::uint64_t> seq;            // the write lock, see section 4
    std::atomic<std::uint64_t> generation;     // counts every write
    std::atomic<std::uint32_t> wake_word;      // used to wake waiters, see section 5
    std::atomic<std::uint32_t> waiters;
    std::atomic<std::int64_t> writer_pid;      // who holds the write lock
    StoredField fields[kMaxFields];            // offset, capacity, kind of each field
    std::atomic<std::uint64_t> versions[kMaxFields];  // counts writes per field
};
```

Each field has a fixed place and a fixed size in the record. A number takes
8 bytes. Text and raw bytes take a 4-byte length followed by up to
`capacity` bytes:

```text
record: | count (8) | ratio (8) | label: length (4) + up to 32 bytes, padded to 8 |
```

Why fixed places instead of something more flexible, such as a dictionary
stored in shared memory:

- Reading or writing a field is one copy of a known number of bytes to or
  from a known address. There is no structure to walk and nothing to
  rebalance.
- Values are stored as plain bytes the Python side encodes (`struct` for
  numbers, UTF-8 for text). Nothing is ever unpickled, so bytes written by
  another process cannot run code in this one.
- A process that opens the block with a different version of the class is
  refused: the `schema_hash` in the header must match the hash the opener
  computes from its own class.

The record's position is stored as an offset rather than as an address,
because each process maps the block at a different address. Boost's
`get_handle_from_address` and `get_address_from_handle` convert between the
two.

Sources:
- Boost.Interprocess, "Mapping Address Independent Pointer: offset_ptr",
  for why addresses cannot be stored in shared memory:
  https://www.boost.org/doc/libs/latest/doc/html/interprocess/offset_ptr.html

## 3. Never trust the shared copy of the layout

Any process that can open the block can also write to it, including the
header. If the code read a field's offset from the header every time it
copied data, another process could change that offset between the check and
the copy and make this process read or write outside the block.

So when a process opens an existing block, it checks every field once and
keeps its own copy:

```cpp
for (std::uint32_t i = 0; i < count; ++i) {
    StoredField f = h->fields[i];              // copy first, then check the copy
    if (!field_fits(f.offset, f.capacity, f.kind, record_size))
        throw SchemaMismatch("segment '" + name + "' has a corrupt header");
    impl->fields.push_back(f);                 // only this copy is used afterwards
}
```

Every later copy of field data uses `impl->fields`, never `h->fields`.

The same idea applies to values arriving from Python: `write()` checks the
size of every value before it takes the write lock, so a bad value in a
multi-field update changes nothing.

There is one limit. The total size of the block comes from Boost's own
bookkeeping, which also lives inside the block. Boost trusts that
bookkeeping for its own lookups, so the check here catches accidental
corruption but not a deliberately forged header. Only a process that can
already write the block could forge one; on Linux that means a process of
the same user, because of the `0600` permissions.

## 4. The write lock: a sequence counter

Readers should never block writers, and neither should need the operating
system in the common case. A counter in the header provides this. The
technique is known as a sequence lock.

The rule: the counter is even when no write is in progress and odd while a
write is running.

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
the reader throws its copy away and tries again. A reader can therefore
never return a mix of old and new bytes, and writes to several fields
(`update(a=..., b=...)`) are seen all at once or not at all.

What the `memory_order` arguments and fences are for, in plain terms:
processors and compilers are allowed to reorder memory accesses when a
single thread cannot tell the difference. Here another process can tell.
The fences forbid the reorderings that would break the rule above: the
writer's data copy may not move before it takes the lock or after it
releases it, and the reader's check of the counter may not move before its
copy.

Why not an ordinary mutex:

- Boost's `interprocess_mutex` on Windows is, by default, a loop that polls
  a value in memory; its native alternative goes through named Windows
  kernel objects. Both cost more than a counter when nobody is competing.
- A reader never makes a writer wait. With a mutex, a slow reader delays
  every writer.
- Writers take turns. One lock per box is enough for records the size of a
  dataclass, and it is what lets `update()` change several fields at once.

The counter must be usable from several processes at once. C++ only
guarantees that for atomic variables the processor updates directly, without
a hidden lock inside the program:

```cpp
static_assert(std::atomic<std::uint64_t>::is_always_lock_free);
```

A writer that waits too long, or a reader that keeps losing to writers,
gives up after `lock_timeout` seconds with `LockTimeoutError`, naming the
process id stored in `writer_pid`. That process may have died while
holding the lock; `force_unlock()` clears it.

Sources:
- Linux kernel documentation, "Sequence counters and sequential locks":
  https://www.kernel.org/doc/html/latest/locking/seqlock.html
- Hans-J. Boehm, "Can Seqlocks Get Along With Programming Language Memory
  Models?", HP Laboratories, 2012, which shows the fence placement used here:
  https://www.hpl.hp.com/techreports/2012/HPL-2012-68.pdf
- cppreference, `std::atomic_thread_fence`:
  https://en.cppreference.com/w/cpp/atomic/atomic_thread_fence
- cppreference, `std::atomic<T>::is_always_lock_free`:
  https://en.cppreference.com/w/cpp/atomic/atomic/is_always_lock_free
- Boost.Interprocess notes on how its mutexes are built on Windows:
  https://www.boost.org/doc/libs/latest/doc/html/interprocess/acknowledgements_notes.html

## 5. Waking a process that waits for a change

`box.events` and `box.watch()` need a way to sleep until another process
writes. Checking in a loop would waste CPU; sleeping for a fixed time would
add delay. Each write therefore increments `generation` and `wake_word`, and
wakes whoever waits on `wake_word`.

On Linux the kernel can put threads to sleep on a 32-bit value in shared
memory and wake them from any process (the futex system call):

```cpp
// waiter: sleep only while wake_word still equals the value it saw
syscall(SYS_futex, &wake_word, FUTEX_WAIT, expected, &timeout, nullptr, 0);

// writer, after each write
wake_word.fetch_add(1);
syscall(SYS_futex, &wake_word, FUTEX_WAKE, INT_MAX, nullptr, nullptr, 0);
```

Windows has a similar call, `WaitOnAddress`, but it only works between
threads of one process, not across processes. So on Windows each box gets a
named semaphore, `Local\sharedbox.<name>.wake`. Waiters register in the
`waiters` counter before sleeping, and a writer releases the semaphore once
per registered waiter:

```cpp
// writer
wake_word.fetch_add(1);
LONG pending = static_cast<LONG>(waiters.load());
if (pending > 0)
    ReleaseSemaphore(semaphore, pending, nullptr);
```

The semaphore is only touched when someone waits, so writes to a box nobody
watches never call into Windows.

A wake-up can never be lost: the waiter reads `wake_word` before it reads
`generation`, and the writer changes `generation` before `wake_word`. If a
write lands between the waiter's two reads, `wake_word` no longer matches
and the wait returns at once instead of sleeping.

The code for this goes in `notifier.hpp` and `notifier.cpp`, which are not in
the repository yet.

Sources:
- `futex(2)` manual page: https://man7.org/linux/man-pages/man2/futex.2.html
- Microsoft, `WaitOnAddress` ("threads within the same process"):
  https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitonaddress
- Microsoft, `CreateSemaphoreW`:
  https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createsemaphorew

## 6. Closing a box while other threads still use it

On a normal Python build, the global interpreter lock (GIL) keeps two
Python threads from running C++ code of this module at the same time. The
free-threaded Python 3.14 build has no GIL, so one thread could call
`close()` and unmap the memory while another thread is copying from it.

Each open box therefore has a reader-writer lock that only protects its own
lifetime. Every operation takes it in shared mode; `close()` takes it
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
reader-writer lock lets new readers in ahead of a waiting writer, so if
`close()` waited first, a stream of reads could keep it waiting forever.
Marking first turns those new reads away with `BoxClosedError`.

This lock is only about one box object inside one process. The sequence
counter from section 4 is what coordinates processes.

Sources:
- PEP 703, "Making the Global Interpreter Lock Optional in CPython":
  https://peps.python.org/pep-0703/
- cppreference, `std::shared_mutex`:
  https://en.cppreference.com/w/cpp/thread/shared_mutex
- `pthread_rwlockattr_setkind_np(3)`, which describes the default
  reader preference on Linux:
  https://man7.org/linux/man-pages/man3/pthread_rwlockattr_setkind_np.3.html

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
already have the block keep using it, and nobody new can open it. On Windows
there is nothing to remove; Windows frees the block when its last user
closes it. This is how Python's `SharedMemory.close()` and
`SharedMemory.unlink()` behave, so the two do not have to be learned
separately.

Sources:
- Python documentation, `multiprocessing.shared_memory`:
  https://docs.python.org/3/library/multiprocessing.shared_memory.html
- `shm_unlink(3)`: https://man7.org/linux/man-pages/man3/shm_unlink.3.html

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
without recompiling; nanobind supports this from Python 3.12, so one wheel
covers 3.12, 3.13, 3.14 and later. `FREE_THREADED` asks for a module that
runs without the GIL; it only takes effect on free-threaded Python.
nanobind ignores whichever option does not fit the interpreter doing the
build, which is why one line produces all three wheels:

| Wheel | Built on | Runs on |
| --- | --- | --- |
| `cp311-cp311` | CPython 3.11 | 3.11 only (the stable ABI needs 3.12) |
| `cp312-abi3` | CPython 3.12 | every normal CPython from 3.12 |
| `cp314-cp314t` | free-threaded CPython 3.14 | free-threaded 3.14 |

Free-threaded Python cannot load stable-ABI modules; a stable ABI for
free-threaded builds (`abi3t`) starts with Python 3.15.

Sources:
- nanobind, "Free-threaded Python":
  https://nanobind.readthedocs.io/en/latest/free_threaded.html
- nanobind, CMake interface (`STABLE_ABI`, `FREE_THREADED`, `NB_DOMAIN`):
  https://nanobind.readthedocs.io/en/latest/api_cmake.html
- PEP 779, "Criteria for supported status for free-threaded Python":
  https://peps.python.org/pep-0779/
- PEP 803, the stable ABI for free-threaded builds (`abi3t`):
  https://peps.python.org/pep-0803/
