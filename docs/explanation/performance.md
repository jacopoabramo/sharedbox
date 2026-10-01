---
icon: lucide/lightbulb
---

# How fast a box is

`benchbox` times sharedbox against the ways the Python standard library
offers to share data between processes. The charts on this page come from
one run of `benchbox all` followed by `benchbox plot`, on Windows 11 with
an AMD Ryzen 9 7945HX, CPython 3.11 and the sharedbox of the `main`
branch after 0.3.1.
[How to run the benchmarks](../how-to/run-benchmarks.md) shows how to make
the same charts on your own machine.

## Single operations

Each bar is the median time of one operation, timed by pyperf in a single
process. The scale is logarithmic: each grid line is ten times the one
before it.

![Time per operation for each contender](../assets/benchmarks/ops-light.svg#only-light)
![Time per operation for each contender](../assets/benchmarks/ops-dark.svg#only-dark)

Reading or writing one [field](glossary.md#field) of a
[box](glossary.md#box) takes 44 to 58 ns. That is 11 to 27 times less than
`mp.Value`, `ShareableList` or `SharedMemory` with a `Lock`, and about 380
to 510 times less than a `Manager().Namespace()`, which sends every access
to another process.

`SharedMemory` with `struct` and no lock is as fast for `int` and `float`
and slower for `str`, but it does nothing to stop a reader from seeing a
value that another process is halfway through writing. A box prevents
that with its [sequence lock](glossary.md#sequence-lock), as
[Reading and writing](reading-and-writing.md) explains.

Changing two fields with [`update`][sharedbox.SharedBox.update] takes 278
ns, about a third of one write under a `Lock`. Reading every field with
[`snapshot`][sharedbox.SharedBox.snapshot] takes 184 ns. `SharedMemory`
with `struct` and no lock packs or unpacks the same values in 60 ns and
129 ns, again without keeping a reader from seeing half of a write.

??? note "The numbers in the chart"

    | operation | SharedBox | SharedMemory+struct | SharedMemory+struct+Lock | mp.Value/Array | ShareableList | Manager().Namespace() |
    | --- | --- | --- | --- | --- | --- | --- |
    | write int | 58 ns | 51 ns | 782 ns | 646 ns | 1.4 us | 25.6 us |
    | read int | 51 ns | 47 ns | 789 ns | 624 ns | 916 ns | 25.1 us |
    | write float | 58 ns | 51 ns | 781 ns | 634 ns | 1.4 us | 22.4 us |
    | read float | 47 ns | 51 ns | 797 ns | 628 ns | 917 ns | 23.9 us |
    | write str | 55 ns | 88 ns | 837 ns | 658 ns | 1.5 us | 21.1 us |
    | read str | 44 ns | 80 ns | 825 ns | 628 ns | 1.0 us | 21.7 us |
    | update two fields | 278 ns | 60 ns | 798 ns | 1.3 us (a lock per value) | 2.8 us | 49.8 us |
    | read all | 184 ns | 129 ns | 940 ns | 2.0 us (a lock per value) | 3.0 us | 71.6 us |

## A change sent to another process

Each row is the time from writing a value in one process until a second
process has seen it and answered back, over 5000 round trips. The dot is
the median (p50), and the line runs to the 99th percentile (p99).

![Round trip time for each contender](../assets/benchmarks/roundtrip-light.svg#only-light)
![Round trip time for each contender](../assets/benchmarks/roundtrip-dark.svg#only-dark)

[`watch`][sharedbox.SharedBox.watch] answers in 39 us at the median and
121 us at p99. `mp.Event` takes 58 us and 148 us, and `mp.Pipe` 37 us and
222 us. A loop that keeps reading `SharedMemory` answers in about 1 us,
but it keeps one CPU core busy on each side the whole time, while a
waiting [watcher](glossary.md#watcher) uses none;
[Waiting for changes](waiting-for-changes.md) explains how it waits.
