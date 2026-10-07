---
icon: lucide/wrench
---

# How to run the benchmarks

The `benchbox` command measures sharedbox on your own machine: single
operations, change notification between two processes, several processes
sharing one value, and wheel size.

## Before you start

Install the `benchmarks` extra, as
[How to install sharedbox](install-sharedbox.md#add-the-benchmark-command)
shows. In a checkout of the repository, `uv sync` installs the
`benchmarks` dependency group, which has the same packages, so
`uv run benchbox` works there too.

## Time single operations

```bash
benchbox ops
```

`ops` times reads and writes of a [box](../explanation/glossary.md#box),
and a read of a
[reference field](../explanation/glossary.md#reference-field), against
the standard library:
`mp.Value` and `mp.Array`, `ShareableList`, `SharedMemory` with `struct`,
and a `Manager().Namespace()`. It runs on
[pyperf](https://pyperf.readthedocs.io). It prints the mean and standard
deviation of each benchmark.

`--fast` makes fewer runs, `--filter` keeps the benchmarks whose name
matches a pattern, and `--json` also writes the results to a file:

```bash
benchbox ops --fast --filter "read*" --json ops.json
```

Arguments after `--` go to pyperf unchanged.

## Time a change sent to another process

```bash
benchbox roundtrip
```

`roundtrip` sends a counter to a second process and times the answer. It
prints the 50th, 90th and 99th percentile and the maximum, in
microseconds, for [`watch`][sharedbox.SharedBox.watch], `mp.Event`,
`mp.Pipe` and a loop polling `SharedMemory`, which keeps one CPU core busy
on each side.

## Time several processes sharing one value

```bash
benchbox contention
```

`contention` starts several writer and reader processes at once and times
every operation each of them runs, so its numbers show how much the
processes slow each other down. A writer stores an `int` and a reader reads
it. For each combination of writer and reader counts, it prints the
throughput of the writers together and of the readers together, in millions
of operations per second, and the 50th and 99th percentile time of one
operation, in nanoseconds. Each time includes reading the clock once.

It runs four contenders: a box whose writers all write the same
[field](../explanation/glossary.md#field), a box where each writer writes a
field of its own, `mp.Value`, and `SharedMemory` with `struct` and a `Lock`.
A box has one lock for its whole record, so the second contender shows
whether writers to different fields still wait for each other.

By default it runs 1, 2 and 4 writers, each with 0 and 2 readers, and every
process runs 100,000 operations. `--writers` and `--readers` take one count
and can be repeated, `--ops` sets the operations per process, `--json` also
writes the results to a file, and `--markdown` prints a Markdown table:

```bash
benchbox contention --writers 1 --writers 8 --readers 0 --ops 50000
```

## Measure wheel size

```bash
benchbox size dist/*.whl
```

`size` prints the size of each wheel and of the extension module inside
it. Without arguments it measures the wheels in `dist/` and `wheelhouse/`.

## Run everything

```bash
benchbox all --out results
```

`all` runs `ops`, `roundtrip` and `size`, writes each command's JSON output
to `results`, and prints a Markdown summary that it also saves as
`results/summary.md`. The summary names the OS, CPU, Python version and
build, and the sharedbox version. `all` measures wheel sizes only when it
finds wheels in `dist/` or `wheelhouse/`, and then measures every wheel
there, older builds included.

`python -m sharedbox.benchmarks` runs the same command as `benchbox`.

## Draw the results

```bash
benchbox plot results
```

`plot` reads `ops.json` and `roundtrip.json` from the folder `all` wrote
and saves two charts next to them as SVG, each in a light and a dark
variant: `ops-light.svg`, `ops-dark.svg`, `roundtrip-light.svg` and
`roundtrip-dark.svg`. The dark files suit a page with a dark background.
[How fast a box is](../explanation/performance.md) shows the charts of one
run.

## Run the pytest benchmarks

The pytest benchmarks are separate and live only in the repository:

```bash
uv run pytest benchmarks --codspeed
```

CI runs them on CodSpeed for every push and pull request to `main`.
