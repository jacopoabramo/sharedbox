---
icon: lucide/lightbulb
---

# Waiting for changes

[`events`][sharedbox.SharedBox.events] and
[`watch`][sharedbox.SharedBox.watch] need a way to sleep until another
process writes. Checking in a loop would waste CPU, and sleeping for a fixed
time would add delay. Each waiting thread therefore holds a
[waiter slot](glossary.md#waiter-slot) in the
[segment](glossary.md#segment), and a write wakes the slots that are
occupied. This page explains how the wake-up works, why changes reach
callbacks the way they do, and what following
[reference fields](glossary.md#reference-field) costs.

## Waking a process

- A [box](glossary.md#box) has a fixed number of slots, set by its
  `max_waiters` class keyword (see [`SharedBox`][sharedbox.SharedBox]), and
  shared by every process. Each waiting [watcher](glossary.md#watcher)
  holds one. A slot records its owner's pid, start time and pid namespace.
- Linux wakes through one futex word in the header:[^futex] a waiter
  sleeps with `FUTEX_WAIT` while `wake_word` still holds the value it read,
  and a writer increments the word and calls `FUTEX_WAKE` once for
  everybody. Windows has `WaitOnAddress`, but it only works between threads
  of one process,[^wait-on-address] so each slot gets its own auto-reset
  event, `Local\sharedbox.<name>.w<i>`,[^create-event] and a writer sets
  the event of every occupied slot. On both systems a write to a box nobody
  waits on makes no system call to wake anyone: the writer reads `waiters`
  and stops at 0.
- A slot held by a process that was killed is freed by the next register
  or attach, except in the cases listed under [Waiter
  slots](../reference/segment-layout.md#waiter-slots). That process checks
  each occupied slot's owner as [Checking a process is
  alive](checking-a-process-is-alive.md) describes. A slot recorded in
  another pid namespace is never freed, because its pid cannot be checked
  from here.
- The Python watcher waits in steps of at most 1 s. The step is not a poll
  for changes: writes and an interrupt wake it at once. After each step the
  watcher checks that its slot still records its own pid, start time and
  namespace, and claims a new slot if not. What it does while every slot is
  taken is described under `max_waiters` in
  [`SharedBox`][sharedbox.SharedBox].
- [`close`][sharedbox.SharedBox.close] interrupts its own watcher: it sets
  the interrupt flag of the watcher's slot and wakes that slot, so the
  watcher thread returns at once instead of at the end of its step.

A wake-up is never lost. On Linux the waiter reads `wake_word` before it
reads the generation, and the writer changes the generation before
`wake_word`. If a write lands between the waiter's two reads, `wake_word`
no longer matches and the wait returns at once.[^futex] On Windows each
event belongs to one slot, so one waiter cannot take the wake-up meant for
another.

[Waiter slots](../reference/segment-layout.md#waiter-slots) gives the order of
the stores that claim, free and release a slot, and the few steps at which
a killed process leaves a slot stuck or the count one too high.

## How changes reach callbacks

The rules for when a signal of `events` is emitted are in the docstring of
[`events`][sharedbox.SharedBox.events]. They follow from how the watcher
works:

- The watcher thread is the one that wakes, so callbacks run on it, not on
  the thread or in the process that wrote.
- After waking, the watcher looks at the write count of each
  [field](glossary.md#field) in the segment. For a field whose count
  moved, it reads the current value and compares it with the value it saw
  last. Several writes between two wake-ups therefore give one emission
  with the latest value, and a write that leaves the value unchanged gives
  none.
- One watcher thread per box [handle](glossary.md#handle) serves both
  `events` and `watch`, so a slow callback delays the `watch` iterators of
  the same box.

`events` is an ordinary psygnal `SignalGroup`, so psygnal's own tools for
controlling emissions apply to it:

- `psygnal.qt.start_emitting_from_queue()` starts a Qt timer on the calling
  thread that runs callbacks connected with `thread="main"`, so a Qt
  application does not call `emit_queued()` itself.
- `blocked()` (or `block()` and `unblock()`) on a signal drops its
  emissions in this process while it is blocked.
- `paused()` holds emissions made inside a block and releases them at the
  end, in order; with a reducer they are combined into one emission.
- `psygnal.throttled` and `psygnal.debounced` limit how often a callback
  runs, for fields that change many times a second:

```python
import time

from psygnal import throttled

from sharedbox import SharedBox


class Pump(SharedBox):
    flow: float = 0.0


@throttled(timeout=100)
def show_flow(new: float, old: float) -> None:
    print("flow", new)


pump = Pump()
pump.events.flow.connect(show_flow)
for i in range(20):
    pump.flow = float(i)
    time.sleep(0.01)
time.sleep(0.3)  # prints a few values, not twenty
pump.close()
Pump.unlink()
```

## Forwarding costs

[`follow`][sharedbox.BoxEvents.follow] waits for changes in each followed
box the same way `events` does: it attaches its own handle to the box,
and that handle's watcher takes one waiter slot in the box and one thread
in the following process, at every level of a chain. The slot counts
against that box's `max_waiters`, and on Linux each handle also takes a file descriptor. `follow()` over a
wide graph can take every waiter slot of a box, and a watcher that finds
none checks for changes once a second.

One thread waiting on several boxes at once (`futex_waitv` on Linux,
`WaitForMultipleObjects` on Windows) would need new native code, and is
left until numbers like the ones below show that the thread count matters.

[`tests/stress/test_stress_follow.py`](https://github.com/jacopoabramo/sharedbox/blob/main/tests/stress/test_stress_follow.py)
runs one `follow()` over W chains of D boxes, W x D followed boxes in all,
with the writer in the same process. Idle CPU is the process time over 2 s
with nothing written; on Windows `time.process_time()` advances in steps of
15.6 ms. A move is the time from assigning a new chain to the first field
until forwarding holds a slot in every box of the new chain and none in the
old one.

Windows 11, CPython 3.11, Release build, 200 writes and 20 moves per row:

| W x D | Threads | Waiter slots | Idle CPU over 2 s | Write to callback, p50 / p99 | Move, p50 / p99 |
| --- | --- | --- | --- | --- | --- |
| 1 x 1 | 1 | 1 | 0 s | 7.9 / 51.0 us | 0.52 / 0.92 ms |
| 1 x 2 | 2 | 2 | 0 s | 7.7 / 63.7 us | 0.54 / 1.21 ms |
| 1 x 3 | 3 | 3 | 0 s | 7.3 / 108.4 us | 1.14 / 1.27 ms |
| 16 x 1 | 16 | 16 | 0 s | 7.6 / 65.3 us | 0.54 / 0.56 ms |
| 16 x 2 | 32 | 32 | 0 s | 8.5 / 106.5 us | 0.54 / 1.17 ms |
| 16 x 3 | 48 | 48 | 0 s | 9.1 / 66.3 us | 1.12 / 1.66 ms |
| 64 x 1 | 64 | 64 | 0 s | 14.2 / 56.7 us | 0.55 / 0.96 ms |
| 64 x 2 | 128 | 128 | 0 s | 8.9 / 78.9 us | 0.54 / 1.11 ms |
| 64 x 3 | 192 | 192 | 0.031 s | 10.3 / 72.9 us | 1.10 / 1.58 ms |

Linux, GitHub Actions `ubuntu-latest`, CPython 3.12 wheel (CI run
36752927114), 200 writes and 20 moves per row:

| W x D | Threads | Waiter slots | Idle CPU over 2 s | Write to callback, p50 / p99 | Move, p50 / p99 |
| --- | --- | --- | --- | --- | --- |
| 1 x 1 | 1 | 1 | 0.000 s | 22.4 / 35.7 us | 0.30 / 0.79 ms |
| 1 x 2 | 2 | 2 | 0.000 s | 20.8 / 33.5 us | 0.68 / 1.18 ms |
| 1 x 3 | 3 | 3 | 0.000 s | 22.1 / 36.3 us | 1.03 / 1.22 ms |
| 16 x 1 | 16 | 16 | 0.001 s | 32.5 / 80.9 us | 0.29 / 0.60 ms |
| 16 x 2 | 32 | 32 | 0.002 s | 25.1 / 63.8 us | 0.72 / 0.93 ms |
| 16 x 3 | 48 | 48 | 0.003 s | 28.6 / 50.6 us | 1.02 / 1.24 ms |
| 64 x 1 | 64 | 64 | 0.004 s | 28.7 / 62.5 us | 0.30 / 0.74 ms |
| 64 x 2 | 128 | 128 | 0.007 s | 30.4 / 64.3 us | 0.72 / 0.81 ms |
| 64 x 3 | 192 | 192 | 0.009 s | 28.8 / 51.0 us | 1.00 / 1.35 ms |

## Sources

[^futex]:
    Linux manual page `futex(2)`: `FUTEX_WAIT` sleeps only while the value
    still equals the expected one; shared (non-private) futexes work
    across processes.
    <https://man7.org/linux/man-pages/man2/futex.2.html>

[^wait-on-address]:
    Microsoft, `WaitOnAddress`: works between threads of the same process.
    <https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitonaddress>

[^create-event]:
    Microsoft, `CreateEventW`: named events shared between processes,
    auto-reset events.
    <https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createeventw>
