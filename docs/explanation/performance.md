---
icon: lucide/lightbulb
---

# How fast a box is

How much faster is a box than what the standard library already gives you?
This page answers with measurements: `benchbox` times `sharedbox` against
each way the standard library offers to share data between processes. The
charts come from one run of `benchbox all` followed by `benchbox plot`, on
Windows 11 with an AMD Ryzen 9 7945HX, CPython 3.11 and a development
version of `sharedbox` after 0.3.1, so your own numbers will differ.
[How to run the benchmarks](../how-to/run-benchmarks.md) shows how to make
the same charts on your own machine.

## Single operations

Each bar is the median time of one operation, timed by pyperf in a single
process. The scale is logarithmic: each grid line is ten times the one
before it.

![Time per operation for each contender](../assets/benchmarks/ops-light.svg#only-light)
![Time per operation for each contender](../assets/benchmarks/ops-dark.svg#only-dark)

Reading or writing one [field](glossary.md#field) of a
[box](glossary.md#box) takes 44 to 59 ns. That is 11 to 26 times less than
`mp.Value`, `ShareableList` or `SharedMemory` with a `Lock`, and about 400
to 620 times less than a `Manager().Namespace()`, which sends every access
to another process.

`SharedMemory` with `struct` and no lock is as fast for `int` and `float`
and slower for `str`, but it does nothing to stop a reader from seeing a
value that another process is halfway through writing. A box prevents
that with its [sequence lock](glossary.md#sequence-lock), as
[Reading and writing](reading-and-writing.md) explains.

Changing two fields with [`update`][sharedbox.SharedBox.update] takes 108
ns, about a seventh of one write under a `Lock`. That is less than two
separate writes, which take about 117 ns together. Reading every field with
[`snapshot`][sharedbox.SharedBox.snapshot] takes 102 ns. `SharedMemory`
with `struct` and no lock packs or unpacks the same values in 60 ns and
131 ns, again without keeping a reader from seeing half of a write.

??? note "The numbers in the chart"

    | operation | SharedBox | SharedMemory+struct | SharedMemory+struct+Lock | mp.Value/Array | ShareableList | Manager().Namespace() |
    | --- | --- | --- | --- | --- | --- | --- |
    | write int | 58 ns | 51 ns | 802 ns | 647 ns | 1.4 us | 28.8 us |
    | read int | 45 ns | 47 ns | 782 ns | 630 ns | 969 ns | 22.8 us |
    | write float | 59 ns | 50 ns | 807 ns | 650 ns | 1.4 us | 23.5 us |
    | read float | 46 ns | 51 ns | 808 ns | 631 ns | 964 ns | 28.3 us |
    | write str | 59 ns | 86 ns | 847 ns | 668 ns | 1.5 us | 28.5 us |
    | read str | 44 ns | 82 ns | 859 ns | 643 ns | 1.1 us | 26.0 us |
    | update two fields | 108 ns | 60 ns | 805 ns | 1.3 us (a lock per value) | 2.9 us | 45.8 us |
    | read all | 102 ns | 131 ns | 929 ns | 2.0 us (a lock per value) | 3.1 us | 70.3 us |

## Large values

In the same run, reading a 1 MiB array field took 190 us and writing one
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
the median (p50), and the line runs to the 99th percentile (p99).

![Round trip time for each contender](../assets/benchmarks/roundtrip-light.svg#only-light)
![Round trip time for each contender](../assets/benchmarks/roundtrip-dark.svg#only-dark)

[`watch`][sharedbox.SharedBox.watch] answers in 54 us at the median and
155 us at p99. `mp.Event` takes 61 us and 183 us, and `mp.Pipe` 44 us and
256 us. A loop that keeps reading `SharedMemory` answers in about 0.4 us,
but it keeps one CPU core busy on each side the whole time, while a
waiting [watcher](glossary.md#watcher) uses none;
[Waiting for changes](waiting-for-changes.md) explains how it waits.
