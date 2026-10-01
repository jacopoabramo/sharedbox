---
icon: lucide/lightbulb
---

# How fast a box is

`benchbox` times sharedbox against the ways the Python standard library
offers to share data between processes. The charts on this page come from
one run of `benchbox all` followed by `benchbox plot`, on Windows 11 with
an AMD Ryzen 9 7945HX, CPython 3.11 and sharedbox 0.3.1.
[How to run the benchmarks](../how-to/run-benchmarks.md) shows how to make
the same charts on your own machine.

## Single operations

Each bar is the median time of one operation, timed by pyperf in a single
process. The scale is logarithmic: each grid line is ten times the one
before it.

![Time per operation for each contender](../assets/benchmarks/ops-light.svg#only-light)
![Time per operation for each contender](../assets/benchmarks/ops-dark.svg#only-dark)

Reading or writing one [field](glossary.md#field) of a
[box](glossary.md#box) takes 40 to 60 ns. That is 11 to 27 times less than
`mp.Value`, `ShareableList` or `SharedMemory` with a `Lock`, and about 380
to 500 times less than a `Manager().Namespace()`, which sends every access
to another process.

`SharedMemory` with `struct` and no lock is as fast for `int` and `float`
and slower for `str`, but it does nothing to stop a reader from seeing a
value that another process is halfway through writing. A box prevents
that with its [sequence lock](glossary.md#sequence-lock), as
[Reading and writing](reading-and-writing.md) explains.

Changing two fields with [`update`][sharedbox.SharedBox.update] takes 651
ns, about as long as one write under a `Lock`. Reading every field with
[`snapshot`][sharedbox.SharedBox.snapshot] takes 216 ns.

??? note "The numbers in the chart"

    | operation | SharedBox | SharedMemory+struct | SharedMemory+struct+Lock | mp.Value/Array | ShareableList | Manager().Namespace() |
    | --- | --- | --- | --- | --- | --- | --- |
    | write int | 57 ns | 51 ns | 801 ns | 640 ns | 1.4 us | 25.6 us |
    | read int | 52 ns | 47 ns | 824 ns | 630 ns | 932 ns | 24.0 us |
    | write float | 57 ns | 50 ns | 777 ns | 639 ns | 1.4 us | 22.6 us |
    | read float | 47 ns | 51 ns | 786 ns | 625 ns | 902 ns | 22.6 us |
    | write str | 55 ns | 88 ns | 846 ns | 660 ns | 1.5 us | 20.9 us |
    | read str | 42 ns | 80 ns | 827 ns | 628 ns | 1.0 us | 21.1 us |
    | update two fields | 651 ns | 60 ns | 791 ns | 1.3 us (a lock per value) | 2.8 us | 43.2 us |
    | read all | 216 ns | 132 ns | 950 ns | 2.0 us (a lock per value) | 3.0 us | 74.0 us |

## A change sent to another process

Each row is the time from writing a value in one process until a second
process has seen it and answered back, over 5000 round trips. The dot is
the median (p50), and the line runs to the 99th percentile (p99).

![Round trip time for each contender](../assets/benchmarks/roundtrip-light.svg#only-light)
![Round trip time for each contender](../assets/benchmarks/roundtrip-dark.svg#only-dark)

[`watch`][sharedbox.SharedBox.watch] answers in 28 us at the median and
77 us at p99. `mp.Event` takes 40 us and 103 us, and `mp.Pipe` 27 us and
226 us. A loop that keeps reading `SharedMemory` answers in under 1 us,
but it keeps one CPU core busy on each side the whole time, while a
waiting [watcher](glossary.md#watcher) uses none;
[Waiting for changes](waiting-for-changes.md) explains how it waits.
