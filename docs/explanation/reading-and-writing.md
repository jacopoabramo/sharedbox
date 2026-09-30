---
icon: lucide/lightbulb
---

# Reading and writing

This page explains how a process reads and writes a [box](glossary.md#box)
safely while other processes use the same
[segment](glossary.md#segment): it never trusts the shared copy of the
layout, and it takes a [sequence lock](glossary.md#sequence-lock) to write.

## Never trust the shared copy of the layout

Any process that can open the block can also write to it, including the
header. If the code read a [field](glossary.md#field)'s offset from the
header every time it copied data, another process could change that offset
between the check and the copy and make this process read or write outside
the block. This is the "time of check, time of use" mistake.[^toctou]

So when a process opens an existing block, it copies line 0 of the header
and the field table once, checks the copies against the size of the
mapping as the OS reports it (`fstat` on Linux, `VirtualQuery` on Windows),
and uses only the copies afterwards. The header's own `size` field must
match the mapping size, but the mapping size comes from the OS, not from
the block. The same idea applies to values arriving from Python: the
native write checks the size of every value before it takes the write
lock, so a bad value in a multi-field update changes nothing.

## The write lock: a sequence counter

Readers should never block writers, and neither should need the operating
system in the common case. A counter in the header provides this. The
technique is known as a sequence lock:[^seqlock] the counter is even when
no write is in progress and odd while a write is running.

A writer moves the counter from even to odd with one compare-and-swap,
copies its bytes, and moves it to the next even value. A reader takes no
lock: it notes the counter, copies, and checks that the counter did not
move. If a write happened while the reader was copying, the reader throws
its copy away and tries again.[^seqlock] A reader can therefore never
return a mix of old and new bytes, and writes to several fields
([`update(a=..., b=...)`][sharedbox.SharedBox.update]) are seen all at
once or not at all. [Sequence
lock](../reference/segment-layout.md#sequence-lock) gives the exact steps and
orderings, including why the unlock is a compare-and-swap rather than a
plain store.

The code needs `memory_order` arguments and fences because processors and
compilers are allowed to reorder memory accesses when a single thread
cannot tell the difference.[^memory-order] Here another
process can tell. The fences forbid the reorderings that would break the
rule above: the writer's data copy may not move before it takes the lock or
after it releases it, and the reader's check of the counter may not move
before its copy. This placement of the fences, a release fence after the
writer takes the counter and an acquire fence between the reader's copy and
its second look at the counter, is the one Boehm shows to be correct for
C++.[^boehm]

sharedbox does not use an ordinary mutex, for three reasons:

- A reader never makes a writer wait. With a mutex, a slow reader delays
  every writer.
- A process that dies holding a mutex leaves it held in a way other
  processes cannot always detect. A dead writer here leaves an odd counter
  and its pid in `writer_pid`, which
  [`force_unlock`][sharedbox.SharedBox.force_unlock] clears.
- Writers take turns. One lock per box is enough for records the size of a
  dataclass, and it is what lets `update` change several fields at once.

The counter must be usable from several processes at once. C++ only
guarantees that for atomic operations the processor performs directly,
without a hidden lock inside the program:[^lock-free]

```cpp
static_assert(std::atomic_ref<std::uint64_t>::is_always_lock_free);
```

## Waiting for the lock

A writer that waits too long, or a reader that keeps losing to writers,
gives up after `lock_timeout` seconds with
[`LockTimeoutError`][sharedbox.LockTimeoutError], naming the process id
stored in `writer_pid`. That process may have died while holding the lock,
and `force_unlock` releases it.

The wait for the lock spins, then yields, then sleeps for a doubling
interval of at most 1 ms. On Windows a sleep ends on a timer tick, so a
wait lasts at least one tick; the Notes of [`SharedBox`][sharedbox.SharedBox]
give its length. In the stress tests on GitHub Actions runners (CI runs
36440952244 and 36443594984), a 1 ms `lock_timeout` ended after 15.7 and
15.8 ms on Windows (median of 200 waits), against 1.18 and 1.16 ms on
Linux. A lock holder that is descheduled for one timer tick can also make
a short timeout expire: with a 10 ms `lock_timeout` and two contending
writers, the Windows runner timed out 1 of 4.3 million writes in the first
run and none of 2.9 million in the second.

## Sources

[^toctou]:
    MITRE CWE-367, "Time-of-check Time-of-use (TOCTOU) Race Condition".
    <https://cwe.mitre.org/data/definitions/367.html>

[^seqlock]:
    Linux kernel documentation, "Sequence counters and sequential locks":
    the even/odd counter, readers that retry.
    <https://www.kernel.org/doc/html/latest/locking/seqlock.html>

[^memory-order]:
    cppreference, `std::memory_order`: what the compiler and processor may
    reorder, and what acquire and release forbid.
    <https://en.cppreference.com/w/cpp/atomic/memory_order>

[^boehm]:
    Hans-J. Boehm, "Can Seqlocks Get Along With Programming Language Memory
    Models?", HP Laboratories technical report HPL-2012-68, 2012: which
    fences make a sequence lock correct in C++.
    <https://www.hpl.hp.com/techreports/2012/HPL-2012-68.pdf>

[^lock-free]:
    cppreference, `std::atomic_ref<T>::is_always_lock_free`.
    <https://en.cppreference.com/w/cpp/atomic/atomic_ref/is_always_lock_free>
