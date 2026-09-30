---
icon: lucide/wrench
---

# How to install sharedbox

PyPI has compiled wheels of sharedbox, so installing it needs no
compiler.

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

Each platform has three wheels, and the installer picks the one that
matches your Python:

| Python | Wheel tag |
| --- | --- |
| CPython 3.11 | `cp311-cp311` |
| CPython 3.12 and newer | `cp312-abi3` |
| Free-threaded CPython 3.14 | `cp314-cp314t` |

They are built for Windows x64 and for Linux x86_64, with glibc and with
musl. [How the module is built](../explanation/how-the-module-is-built.md)
explains why there are three.

## Add the benchmark command

The `benchmarks` extra installs the `benchbox` command, which measures
sharedbox on your own machine:

```bash
uv add "sharedbox[benchmarks]"
```

or, with pip:

```bash
pip install "sharedbox[benchmarks]"
```

[How to run the benchmarks](run-benchmarks.md) shows its commands.
