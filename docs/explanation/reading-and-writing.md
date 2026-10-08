---
icon: lucide/lightbulb
---

# Reading and writing

Several processes reading and writing the same memory at once is how data
gets mixed up: a reader can catch a writer halfway, or a buggy process can
scribble over the layout everyone relies on. This page explains the two
habits that keep a [box](glossary.md#box) safe while other processes use
the same [segment](glossary.md#segment): a process never trusts the shared
copy of the layout, and it writes only under a
[sequence lock](glossary.md#sequence-lock).

## Never trust the shared copy of the layout

Any process that can open the block can also write to it, header included.
So if the code read a [field](glossary.md#field)'s offset from the header
every time it copied data, another process could change that offset between
the check and the copy, and make this process read or write outside the
block. This mistake has a name, "time of check, time of use".[^toctou]

To rule it out, a process that opens an existing block copies line 0 of the
header and the field table once, checks those copies against the size of
the mapping as the operating system reports it (`fstat` on Linux,
`VirtualQuery` on Windows), and from then on uses only its own copies. The header's own `size` field must
match the mapping size, but the mapping size comes from the OS, not from
the block. The same idea applies to values arriving from Python: the
native write checks the size of every value before it takes the write
lock, so a bad value in a multi-field update changes nothing.

## The write lock: a sequence counter

The lock has two jobs: a reader must never hold up a writer, and neither
should need a call into the operating system in the usual case. A single
counter in the header does both. The technique is called a sequence
lock:[^seqlock] the counter is even when no write is in progress and odd
while one is running.

Step through a write that lands in the middle of a read, and point at a
shape to read what it does:

```d2 title="A read that meets a write"
...@diagrams/style
label: "The reader notes the counter: 4 is even, so no write is running."
grid-columns: 1
vertical-gap: 30
lock: "" {
  style.stroke-width: 0
  style.fill: transparent
  grid-rows: 2
  grid-columns: 3
  horizontal-gap: 110
  vertical-gap: 40
  writer: "writer" {
    class: process
    tooltip: Any process that assigns a field or calls update.
  }
  seq: "seq = 4" {
    class: current
    tooltip: The counter in the header. Even means no write is running, odd means one is.
  }
  reader: "reader" {
    class: process
    tooltip: Any process reading a field. It never changes the counter, so it never holds up a writer.
  }
  g1: {class: gap}
  record: "record" {
    class: file
    tooltip: The bytes of every field, which readers copy and writers overwrite.
  }
  g2: {class: gap}
  reader -> seq: "notes 4"
  writer -> seq: "swaps 4 for 5" {style.opacity: 0}
  writer -> record: "writes" {style.opacity: 0}
  reader -> record: "copies" {style.opacity: 0}
}
code: "" {
  style.stroke-width: 0
  style.fill: transparent
  grid-columns: 2
  horizontal-gap: 60
  writer: |cpp
    1    s = seq;
    2    cas(seq, s, s + 1);
    3    copy(values, record);
    4    cas(seq, s + 1, s + 2);
  |
  reader: |cpp
    1 -> s = seq;
    2    copy(record, out);
    3    if (seq != s) retry;
  |
}
steps: {
  1: {
    label: "A writer takes the lock by swapping 4 for 5 in one atomic step."
    lock.seq.label: "seq = 5"
    (lock.writer -> lock.seq)[0].style.opacity: 1
    code.writer: |cpp
      1    s = seq;
      2 -> cas(seq, s, s + 1);
      3    copy(values, record);
      4    cas(seq, s + 1, s + 2);
    |
    code.reader: |cpp
      1 -> s = seq;
      2    copy(record, out);
      3    if (seq != s) retry;
    |
  }
  2: {
    label: "The writer copies its values in while the reader copies the record out, so the reader's copy may mix old and new bytes."
    (lock.writer -> lock.record)[0].style.opacity: 1
    (lock.reader -> lock.record)[0].style.opacity: 1
    code.writer: |cpp
      1    s = seq;
      2    cas(seq, s, s + 1);
      3 -> copy(values, record);
      4    cas(seq, s + 1, s + 2);
    |
    code.reader: |cpp
      1    s = seq;
      2 -> copy(record, out);
      3    if (seq != s) retry;
    |
  }
  3: {
    label: "The writer releases the lock by setting the counter to 6."
    lock.seq.label: "seq = 6"
    (lock.writer -> lock.seq)[0].label: "sets 6"
    code.writer: |cpp
      1    s = seq;
      2    cas(seq, s, s + 1);
      3    copy(values, record);
      4 -> cas(seq, s + 1, s + 2);
    |
    code.reader: |cpp
      1    s = seq;
      2 -> copy(record, out);
      3    if (seq != s) retry;
    |
  }
  4: {
    label: "The reader looks at the counter again: 6, not the 4 it noted, so it throws its copy away and starts over."
    (lock.reader -> lock.seq)[0].label: "now 6: retry"
    lock.reader.class: failed
    code.writer: |cpp
      1    s = seq;
      2    cas(seq, s, s + 1);
      3    copy(values, record);
      4    cas(seq, s + 1, s + 2);
    |
    code.reader: |cpp
      1    s = seq;
      2    copy(record, out);
      3 -> if (seq != s) retry;
    |
  }
  5: {
    label: "On the next try the counter is 6 before and after the copy, so the copy holds one whole write."
    (lock.reader -> lock.seq)[0].label: "still 6: done"
    lock.reader.class: process
    (lock.writer -> lock.seq)[0].style.opacity: 0
    (lock.writer -> lock.record)[0].style.opacity: 0
    code.writer: |cpp
      1    s = seq;
      2    cas(seq, s, s + 1);
      3    copy(values, record);
      4    cas(seq, s + 1, s + 2);
    |
    code.reader: |cpp
      1    s = seq;
      2    copy(record, out);
      3 -> if (seq != s) retry;
    |
  }
}
```

Because the reader always checks the counter again after copying, it never
returns a mix of old and new bytes,[^seqlock] and writes to several fields
([`update(a=..., b=...)`][sharedbox.SharedBox.update]) are seen all at once
or not at all. The writer takes the counter from even to odd with one
compare-and-swap, so two writers can't both win. [Sequence
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
  and its pid in `writer_pid`, so you can see who held the lock, and
  [`force_unlock`][sharedbox.SharedBox.force_unlock] releases it.
- Writers take turns. One lock per box is enough for records the size of a
  dataclass, and it is what lets `update` change several fields at once.

The counter must be usable from several processes at once. C++ only
guarantees that for atomic operations the processor performs directly,
without a hidden lock inside the program,[^lock-free] so the header
checks at compile time that the counter's operations are of that kind:

```cpp
static_assert(std::atomic_ref<std::uint64_t>::is_always_lock_free);
```

[`writing`][sharedbox.SharedBox.writing] holds this same lock for as long
as its block runs, so a reader that starts in the meantime keeps retrying
until the block ends. If that takes longer than the reader's lock timeout,
the reader gives up with [`LockTimeoutError`][sharedbox.LockTimeoutError].
That trade is worth it for a large array a producer can fill in place,
because it saves a full copy, but only while the block stays shorter than
the readers' lock timeout.

## Waiting for the lock

A writer that waits too long for the lock, or a reader that keeps losing
to writers, gives up after `lock_timeout` seconds with
[`LockTimeoutError`][sharedbox.LockTimeoutError], whose message names the
process stored in `writer_pid` as the lock's holder. That process may have
died while holding the lock, and `force_unlock` releases it.

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
