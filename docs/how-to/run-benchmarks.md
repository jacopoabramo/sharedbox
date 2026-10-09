---
icon: lucide/wrench
---

# How to run the benchmarks

Numbers measured on someone else's machine only go so far. The `benchbox`
command measures `sharedbox` on yours, next to the ways the standard
library shares data between processes: single reads and writes, how fast a
change reaches another process, streams of items, several processes sharing
one value, and the size of the installed package.

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
`mp.Pipe`, a loop polling `SharedMemory`, which keeps one CPU core busy on
each side, and [`SharedStream`][sharedbox.SharedStream].

## Time a stream

```bash
benchbox stream
```

`stream` prints two tables. The first is throughput, for a lossless, a lossy
and a latest [`SharedStream`][sharedbox.SharedStream], an `mp.Queue` per
reader and a ring in `SharedMemory` guarded by a lock: the items per second
the sender sent and a reader received, averaged over the readers, and the
items a lossy or latest reader skipped. It runs 1 and 4 readers with items
of 1 KiB and of 512 KiB, repeats each row three times, and prints the median
run. The second table pairs `send` and `asend` with `receive`, `async for`
and `events.received`, next to an `mp.Queue` read with `get` and from
`asyncio`. For each pairing it prints the items per second and the 50th,
90th and 99th percentile of the time from the sender stamping an item until
the reader has it.

`--short` makes a run of a few seconds, with fewer items and one repeat.
`--table` takes `throughput`, `matrix` or `both`, `--markdown` prints
Markdown tables, and `--json` also writes the results to a file:

```bash
benchbox stream --short --table throughput --json stream.json
```

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

`all` runs `ops`, `roundtrip`, `stream` and `size`, writes each command's
JSON output to `results` (`stream.json` holds the two stream tables), and
prints a Markdown summary that it also saves as `results/summary.md`. The
summary names the OS, CPU, Python version and build, and the sharedbox
version. `all` measures wheel sizes only when it finds wheels in `dist/` or
`wheelhouse/`, and then measures every wheel there, older builds included.
On a desktop machine a full run takes about 30 minutes, most of it in `ops`
and `stream`, so start it when you will not need the machine.

`python -m sharedbox.benchmarks` runs the same command as `benchbox`.

## Draw the results

```bash
benchbox plot results
```

`plot` reads `ops.json`, `roundtrip.json` and `stream.json` from the folder
`all` wrote and saves four [plotly](https://plotly.com/python/) figures as
JSON in `charts/` next to them: `ops.json`, `roundtrip.json`,
`stream-throughput.json` and `stream-matrix.json`. It skips a chart whose
results are missing and says which on standard error.

A page of the docs site draws a figure with a `div` of the class
`sbx-chart`, whose `data-src` is the path of the file, relative to the page:

```html
<div class="sbx-chart" data-src="../../assets/benchmarks/ops.json"></div>
```

`docs/javascripts/charts.js` loads plotly.js when a page has such a `div`,
draws the figure and gives it the colours of the page's light or dark theme.
Copy the files to `docs/assets/benchmarks/` to update the charts of
[How fast a box is](../explanation/performance.md), which shows one run.

## Run the pytest benchmarks

The pytest benchmarks are separate and live only in the repository:

```bash
uv run pytest benchmarks --codspeed
```

CI runs them on CodSpeed for every push and pull request to `main`. The
`Ops` and `Streams` workflows time `benchbox ops` and `benchbox stream` on
the base branch and on the pull request, for pull requests that touch the
native code or the stream.
