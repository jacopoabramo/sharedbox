---
icon: lucide/lightbulb
---

# How fast a box is

How much faster is a box than what the standard library already gives you?
This page answers with measurements: `benchbox` times `sharedbox` against
each way the standard library offers to share data between processes. The
charts and tables come from one run of `benchbox all` followed by
`benchbox plot`, on Windows 11 with an AMD Ryzen 9 7945HX, CPython 3.11 and
a development version of `sharedbox` after 0.5.0, so your own numbers will
differ. [How to run the benchmarks](../how-to/run-benchmarks.md) shows how
to make the same charts on your own machine.

## Single operations

Each row of a panel is one contender timing one operation in a single
process, measured by pyperf. The dot is the median, and the line runs from
the 10th to the 90th percentile of the measured values. The scale is
logarithmic: each grid line is ten times the one before it. Hover over a dot
for the exact values.

<div class="sbx-chart" data-src="../assets/benchmarks/ops.json"></div>

Reading or writing one [field](glossary.md#field) of a
[box](glossary.md#box) takes 43 to 61 ns. That is 11 to 25 times less than
`mp.Value`, `ShareableList` or `SharedMemory` with a `Lock`, and about 370
to 540 times less than a `Manager().Namespace()`, which sends every access
to another process.

`SharedMemory` with `struct` and no lock is about as fast for `int` and
`float` and slower for `str`, but it does nothing to stop a reader from
seeing a value that another process is halfway through writing. A box
prevents that with its [sequence lock](glossary.md#sequence-lock), as
[Reading and writing](reading-and-writing.md) explains.

Changing two fields with [`update`][sharedbox.SharedBox.update] takes 94
ns, about an eighth of one write under a `Lock`. That is less than two
separate writes, which take about 117 ns together. Reading every field with
[`snapshot`][sharedbox.SharedBox.snapshot] takes 102 ns. `SharedMemory`
with `struct` and no lock packs or unpacks the same values in 59 ns and
134 ns, again without keeping a reader from seeing half of a write.

??? note "The numbers in the chart"

    | operation | SharedBox | SharedMemory+struct | SharedMemory+struct+Lock | mp.Value/Array | ShareableList | Manager().Namespace() |
    | --- | --- | --- | --- | --- | --- | --- |
    | write int | 60 ns | 47 ns | 787 ns | 641 ns | 1.4 us | 22.8 us |
    | read int | 44 ns | 47 ns | 799 ns | 618 ns | 949 ns | 23.7 us |
    | write float | 58 ns | 50 ns | 795 ns | 638 ns | 1.4 us | 27.5 us |
    | read float | 46 ns | 52 ns | 791 ns | 628 ns | 957 ns | 23.2 us |
    | write str | 61 ns | 86 ns | 851 ns | 663 ns | 1.5 us | 22.7 us |
    | read str | 43 ns | 82 ns | 833 ns | 638 ns | 1.1 us | 22.8 us |
    | update two fields | 94 ns | 59 ns | 803 ns | 1.3 us (a lock per value) | 2.9 us | 48.7 us |
    | read all | 102 ns | 134 ns | 941 ns | 2.0 us (a lock per value) | 3.2 us | 81.8 us |

## Large values

In the same run, reading a 1 MiB array field took 217 us and writing one
took 19 us. The read is slower because it gets fresh memory for a new array
every time, which on Windows costs far more than the copy itself;
[`read_into`][sharedbox.SharedBox.read_into] copies into an array you
already have instead, and costs about what a write does. A box has one
write lock, so while a large value is being written, reads and writes of
every other field of that box wait; keeping a large array in a box of its
own avoids that.

## A change sent to another process

Each row is the time from writing a value in one process until a second
process has seen it and answered back, over 5000 round trips. The dot is
the median (p50), and the line runs to the 99th percentile (p99). The
`SharedStream` row sends an `int` through one [stream](glossary.md#stream)
and gets the answer back through a second one.

<div class="sbx-chart" data-src="../assets/benchmarks/roundtrip.json"></div>

[`watch`][sharedbox.SharedBox.watch] answers in 37 us at the median and
101 us at p99. `mp.Event` takes 57 us and 133 us, and `mp.Pipe` 37 us and
230 us. A `SharedStream` answers in 2.1 us and 5.2 us, about 18 times
faster than `watch` at the median. A loop that keeps reading
`SharedMemory` answers in about 0.6 us, but it keeps one CPU core busy on
each side the whole time, while a waiting [watcher](glossary.md#watcher)
uses none; [Waiting for changes](waiting-for-changes.md) explains how it
waits.

A stream is faster than `watch` because its two ends keep checking for a
short time before they sleep. A reader that finds no item checks again up to
1000 times before it sleeps, and a sender waiting for room does the same.
In a round trip the answer arrives within those checks, so neither process
sleeps and nobody has to wake it. A watcher goes to sleep as soon as
nothing has changed, so every change costs a wake-up by the operating
system. The cost is one core kept busy during those checks. A reader that
waits longer sleeps like a watcher, which the next section shows: with
items 1 ms apart, the reader has gone to sleep before the next one arrives,
and it takes 31 us at the median to see it. That is one way, with one
wake-up; a round trip through `watch` needs two and takes 37 us.

## Streams

A [stream](glossary.md#stream) carries items from one
[sender](glossary.md#sender) to several [readers](glossary.md#reader), so
the comparison is with the ways the standard library moves a series of
items between processes. `mp.Queue` pickles each item and sends it through
a pipe, and the sender needs one queue for each reader. The other contender
is a ring of 32 slots in `SharedMemory` that the sender and every reader
guard with one lock, which is the simplest ring you can write with the
standard library. Every contender holds up to 32 items, and every lossless
reader receives every item.

The items have two sizes: 1 KiB, an array of 1024 bytes next to a counter,
and 512 KiB, a 512 by 512 array of `uint16`, such as an image. A row is the
median of three runs of 20000 items for 1 KiB and 2000 for 512 KiB.
[How to send items through a stream](../how-to/send-items-through-a-stream.md)
shows how a stream is used.

### Items per second

Each panel is one item size and one number of readers. Each dot is the items
per second a reader received, averaged over the readers of the median run,
and the line runs from the slowest to the fastest of the three runs, so a
long line means the runs disagreed.

<div class="sbx-chart" data-src="../assets/benchmarks/stream-throughput.json"></div>

With one reader and 1 KiB items, a [lossless](glossary.md#lossless) reader
receives 511k items per second. `mp.Queue` manages 84k and the ring with a
lock 43k, so the stream is 6 and 12 times faster. The gap grows with the
readers, because a stream's readers do not wait for each other. Four
lossless readers each still receive 429k per second, 84% of the rate of
one, while the sender of the queues has to put every item on four queues
and the readers of the ring share one lock. `mp.Queue` falls to 19k and the
ring to 17k, which makes the stream 22 and 25 times faster.

For 512 KiB items the gap is smaller against the ring and still large
against the queue. One lossless reader receives 25k items per second
against 2.0k for `mp.Queue` and 16k for the ring, which is 12 and 1.5 times
faster. With four readers it is 18k against 1.2k and 7.0k, 15 and 2.6 times
faster. Part of the gap to `mp.Queue` comes from how the readers receive:
the stream's readers copy each item into one array they reuse, with
[`iter_into`][sharedbox.StreamReader.iter_into], while a queue's reader
unpickles every item into a new one.

The reader mode costs little. At 1 KiB with one reader, the
[lossless](glossary.md#lossless), [lossy](glossary.md#lossy) and
[latest](glossary.md#latest) readers receive between 471k and 511k items
per second, within 8% of each other. With four readers lossless is 13%
slower than the other two (429k against 489k and 491k), because a lossless
sender waits for its slowest reader, while a lossy or latest sender never
waits and sent 1.1M to 1.4M items per second. The price is that those
readers skip items: one lossy reader skipped 12,895 of the 20,000. At 512
KiB the three modes differ by less than the runs do (the lossless row with
one reader ranges from 19k to 25k), so the chart cannot tell them apart.

### Sync and async calls

A stream can be read by a blocking call, by `async for`, or by callbacks
connected to `events.received`, and sent with `send` or `asend`. This chart
times every pairing. Each dot is the median latency, the time from the
sender stamping an item until the reader has it, over 500 items sent at
least 1 ms apart, and the line runs from p50 to p99. Hover over a dot for
the p90 and for the items per second the pairing sustained when the sender
did not wait. The `mp.Queue` rows are the standard library baseline, the
last of them reading the queue from `asyncio` with `run_in_executor`.

<div class="sbx-chart" data-src="../assets/benchmarks/stream-matrix.json"></div>

At 1 KiB, blocking `send` and `receive` move 433k items per second. Reading
with `async for` keeps 394k, 91% of that, and `events.received` 257k.
Sending with `asend` is slower: 262k with `receive` and 148k with
`async for`. Reading an `mp.Queue` from `asyncio` with `run_in_executor`
hands every `get` to a thread and reaches 10k items per second, so
`async for` on a stream is about 40 times faster. A blocking `get` reaches
84k.

`async for` stays close to the blocking rate because, when an item is
already waiting, it takes it on the event loop's own thread instead of
handing the wait to the reader's background thread. The row
`async for (buffered)` isolates that: the sender fills the ring and the
reader awaits each of the 32 items, all already there, at 370k items per
second, about what `async for` sustains in the full run. When no item is
waiting, as in the latency part of the run, the wait does go through the
background thread, and the median is 139 us against 31 us for `receive`.

At 512 KiB every pairing sits at 4k to 5k items per second, whichever way it
sends and reads, and `mp.Queue` reaches 2k. That is far below the 25k of the
chart above because these readers use `receive`, `async for` and
`events.received`, which put each item in new memory, twice for an item that
is a record: first the whole item, then its array into an array of its own.
The throughput readers copy every item into one array they reuse, with
[`iter_into`][sharedbox.StreamReader.iter_into]. The row
`async for (buffered)` shows the cost on its own: it only times reading
items already in the ring, and still reaches 4k per second. On this Windows
machine a fresh 512 KiB buffer costs about 100 us, so two of them plus the
copy come to about 240 us per item, which is 4k per second. If your items
are large, read them with `iter_into` or
[`receive_into`][sharedbox.StreamReader.receive_into].

??? note "The numbers in the chart"

    | sender | reader | item | items/s | p50 us | p90 us | p99 us |
    | --- | --- | --- | --- | --- | --- | --- |
    | send | receive | 1 KiB | 433k | 31.2 | 58.2 | 85.6 |
    | send | async for | 1 KiB | 394k | 139.4 | 232.8 | 304.4 |
    | send | events.received | 1 KiB | 257k | 40.5 | 75.0 | 119.9 |
    | asend | receive | 1 KiB | 262k | 47.8 | 71.0 | 115.5 |
    | asend | async for | 1 KiB | 148k | 189.1 | 272.3 | 590.8 |
    | asend | events.received | 1 KiB | 177k | 64.3 | 95.3 | 130.5 |
    | send | async for (buffered) | 1 KiB | 370k | - | - | - |
    | mp.Queue put | mp.Queue get | 1 KiB | 84k | 84.5 | 149.7 | 193.7 |
    | mp.Queue put | asyncio + mp.Queue | 1 KiB | 10k | 195.9 | 305.3 | 425.0 |
    | send | receive | 512 KiB | 4k | 387.4 | 526.7 | 1017.7 |
    | send | async for | 512 KiB | 4k | 289.9 | 521.8 | 836.7 |
    | send | events.received | 512 KiB | 4k | 406.9 | 623.5 | 958.0 |
    | asend | receive | 512 KiB | 4k | 484.1 | 599.8 | 1193.0 |
    | asend | async for | 512 KiB | 5k | 483.2 | 729.9 | 916.2 |
    | asend | events.received | 512 KiB | 5k | 352.0 | 507.6 | 766.8 |
    | send | async for (buffered) | 512 KiB | 4k | - | - | - |
    | mp.Queue put | mp.Queue get | 512 KiB | 2k | 730.1 | 1025.4 | 5783.6 |
    | mp.Queue put | asyncio + mp.Queue | 512 KiB | 2k | 699.6 | 1057.7 | 7775.4 |
