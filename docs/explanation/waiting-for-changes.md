---
icon: lucide/lightbulb
---

# Waiting for changes

When you connect a callback to [`events`][sharedbox.SharedBox.events] or
loop over [`watch`][sharedbox.SharedBox.watch], something has to sleep
until another process writes, then wake up at once. Checking in a loop
would burn CPU, and sleeping for a fixed time would add delay. Instead,
each waiting thread holds a [waiter slot](glossary.md#waiter-slot) in the
[segment](glossary.md#segment), and every write wakes the slots that are
taken. This page explains how that wake-up works, why your callbacks are
called the way they are, and what following
[reference fields](glossary.md#reference-field) costs.

## Waking a process

A [box](glossary.md#box) has a fixed number of slots, set by its
`max_waiters` class keyword (see [`SharedBox`][sharedbox.SharedBox]) and
shared by every process. Each waiting [watcher](glossary.md#watcher) holds
one, and the slot records which process owns it: its pid, start time and
pid namespace.

How a write wakes the waiters depends on the system. Linux has one futex
word in the header:[^futex] a waiter sleeps with `FUTEX_WAIT` for as long
as `wake_word` still holds the value it read. When a waiter is inside a
wait, a writer increments the word and calls `FUTEX_WAKE` once for
everybody. Windows has a similar call, `WaitOnAddress`, but it only works
between threads of one process,[^wait-on-address] so there each slot gets its own auto-reset
event, `Local\SBX:<name>#w<i>`,[^create-event] and a writer sets the
event of every taken slot. Either way, a write to a box nobody is waiting
on costs no system call: a waiter counts itself in `sleepers` while it is
inside a wait, and a writer that sees `sleepers` at 0 stops. A watcher that
holds a slot but is awake does not cost the writer a call either.

A process can be killed while it holds a slot, so the next process that
registers or attaches checks each taken slot's owner, as
[Checking a process is alive](checking-a-process-is-alive.md) describes,
and frees the slots of owners that are gone. A watcher spends nearly all
its time inside a wait, so that is usually where its process is killed.
The slot records which count the wait added itself to, and freeing the
slot takes that 1 back, so later writes stop paying a wake call for a
thread that is gone. The few exceptions are listed
under [Waiter slots](../reference/segment-layout.md#waiter-slots). A slot
recorded in another pid namespace is never freed, because its pid can't be
checked from here.

The Python watcher waits in steps of at most 1 s, yet it still notices a
write at once, because a write or an interrupt wakes it in the middle of a
step. The steps are there for a different reason: after each one, the
watcher checks that its slot still records its own process, and claims a
new slot if not. What it does while every slot
is taken is described under `max_waiters` in
[`SharedBox`][sharedbox.SharedBox]. When you call
[`close`][sharedbox.SharedBox.close], it sets the interrupt flag of its own
watcher's slot and wakes that slot, so the watcher thread returns at once
instead of at the end of its step.

A wake-up is never lost. A waiter counts itself in `sleepers` before it
reads `wake_word` and then the generation. A writer changes the generation,
then reads `sleepers`. If it reads 0, the waiter has not counted itself yet,
so the waiter's read of the generation sees the new value. Otherwise the
writer increments `wake_word` and wakes. On Linux, a write that lands between
the waiter's reads of `wake_word` and the generation has already changed
`wake_word`, so it no longer matches and the wait returns at once.[^futex] On
Windows each event belongs to one slot, so one waiter cannot take the wake-up
meant for another.

[Waiter slots](../reference/segment-layout.md#waiter-slots) gives the order of
the stores that claim, free and release a slot, and the few steps at which
a killed process leaves a slot stuck or the count one too high.

## How changes reach callbacks

Step through one write reaching your callback, and point at a shape to
read what it does:

```d2 title="From a write to your callback"
...@diagrams/style
label: "A writer in any process assigns a field. The write raises the field's write count and wakes the threads waiting in the waiter slots."
grid-columns: 1
vertical-gap: 30
pic: "" {
  style.stroke-width: 0
  style.fill: transparent
  grid-rows: 2
  grid-columns: 3
  horizontal-gap: 110
  vertical-gap: 60
  writer: "writer, any process" {class: process}
  segment: "segment" {
    class: hardware
    tooltip: Holds the values, a write count per field, and the waiter slots of the threads waiting for changes.
  }
  watcher: "watcher thread" {class: [process; hidden]}
  g1: {class: gap}
  callbacks: "events callbacks" {class: [step; hidden]}
  watch: "watch iterators" {class: [step; hidden]}
  writer -> segment: "writes, wakes"
  segment -> watcher: "wakes" {style.opacity: 0}
  watcher -> callbacks: "new, old" {style.opacity: 0}
  watcher -> watch: "new value" {style.opacity: 0}
}
code: "" {
  style.stroke-width: 0
  style.fill: transparent
  code: |python
    1     # a writer, in any process
    2  -> motor.position = 10
    3     # the watcher thread of each box handle
    4     while not stopped:
    5         segment.wait(slot)
    6         for field in fields:
    7             if counts[field] == seen[field]:
    8                 continue
    9             new = read(field)
    10            if new != old[field]:
    11                events[field].emit(new, old[field])
  |
}
steps: {
  1: {
    label: "The watcher thread of each box handle wakes, compares each field's write count with the last one it saw, and reads only the fields whose count moved."
    pic.watcher.class: process
    pic.watcher.tooltip: One thread per box handle, holding a waiter slot. Several writes between two wake-ups give one emission with the latest value.
    (pic.segment -> pic.watcher)[0].style.opacity: 1
    code.code: |python
      1     # a writer, in any process
      2     motor.position = 10
      3     # the watcher thread of each box handle
      4     while not stopped:
      5  ->     segment.wait(slot)
      6         for field in fields:
      7  ->         if counts[field] == seen[field]:
      8                 continue
      9  ->         new = read(field)
      10            if new != old[field]:
      11                events[field].emit(new, old[field])
    |
  }
  2: {
    label: "It calls your events callbacks with the new and old value, on the watcher thread, and hands the new value to watch iterators. A write that leaves the value unchanged emits nothing."
    pic.callbacks.class: step
    pic.callbacks.tooltip: They run on the watcher thread, not in the process or the thread that wrote. The docstring of events lists when each signal fires.
    pic.watch.class: step
    pic.watch.tooltip: Served by the same thread, so a slow callback delays the watch iterators of the same box.
    (pic.watcher -> pic.callbacks)[0].style.opacity: 1
    (pic.watcher -> pic.watch)[0].style.opacity: 1
    code.code: |python
      1     # a writer, in any process
      2     motor.position = 10
      3     # the watcher thread of each box handle
      4     while not stopped:
      5         segment.wait(slot)
      6         for field in fields:
      7             if counts[field] == seen[field]:
      8                 continue
      9             new = read(field)
      10 ->         if new != old[field]:
      11 ->             events[field].emit(new, old[field])
    |
  }
}
```

The exact rules for when a signal fires are in the docstring of
[`events`][sharedbox.SharedBox.events].

`events` is an ordinary `SignalGroup` from the `psygnal` library, so all of
`psygnal`'s own tools for controlling when callbacks run work on it:

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
box the same way `events` does: it opens its own handle on the box, and
that handle's watcher takes one waiter slot in the box and one thread in
your process, at every level of a chain. The slot counts against that
box's `max_waiters`, and on Linux each handle also takes a file descriptor.
So `follow()` over a wide graph can take every waiter slot of a box, and a
watcher that finds none left falls back to checking for changes once a
second.

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
