---
icon: lucide/wrench
---

# How to install sharedbox

`sharedbox` comes as ready-built packages (wheels) on PyPI, so you can
install it without a compiler.

## Before you start

!!! note "What you need"

    CPython 3.11 or newer on Windows x64 or Linux x86_64. macOS is not
    supported.

## Add it to your project

In a project that [`uv`](https://docs.astral.sh/uv/) manages:

```bash
uv add sharedbox
```

With pip, inside a virtual environment:

```bash
pip install sharedbox
```

## Which wheel you get

You don't have to choose a wheel yourself: each platform has three, and the
installer picks the one that matches your Python.

| Python | Wheel tag |
| --- | --- |
| CPython 3.11 | `cp311-cp311` |
| CPython 3.12 and newer | `cp312-abi3` |
| Free-threaded CPython 3.14 | `cp314-cp314t` |

They are built for Windows x64 and for Linux x86_64, both for the common
glibc-based distributions and for musl-based ones such as Alpine.
[How the module is built](../explanation/how-the-module-is-built.md)
explains why there are three.

## Add the benchmark command

If you want to measure `sharedbox` on your own machine, install the
`benchmarks` extra, which adds the `benchbox` command:

```bash
uv add "sharedbox[benchmarks]"
```

or, with pip:

```bash
pip install "sharedbox[benchmarks]"
```

[How to run the benchmarks](run-benchmarks.md) shows its commands.
