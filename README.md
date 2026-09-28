# `sharedbox`

[![PyPI](https://img.shields.io/pypi/v/sharedbox)](https://pypi.org/project/sharedbox/)
[![CI](https://github.com/jacopoabramo/sharedbox/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/jacopoabramo/sharedbox/actions/workflows/ci.yaml)
[![CodSpeed](https://img.shields.io/endpoint?url=https://codspeed.io/badge.json)](https://app.codspeed.io/jacopoabramo/sharedbox?utm_source=badge)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![clang-format](https://img.shields.io/badge/C%2B%2B%20style-clang--format-blue)](https://clang.llvm.org/docs/ClangFormat.html)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)

> [!WARNING]
> This project is a work in progress; be patient or feel free to contribute.

`sharedbox` keeps records in shared memory. Each box is one named segment, and every process that opens it reads and writes the same fields.

## Installation

It is recommended to install `sharedbox` in a virtual environment; for example using `uv`:

```sh
uv venv --python 3.11
.venv\Scripts\activate
uv pip install sharedbox
```

## Quick start

```python
import multiprocessing as mp
from typing import Annotated

from sharedbox import Capacity, SharedBox


class Motor(SharedBox):
    position: int
    enabled: bool
    label: Annotated[str, Capacity(32)]


def worker() -> None:
    motor = Motor.attach()          # finds the box by its class
    motor.position = 10
    motor.close()


if __name__ == "__main__":
    with Motor(1, False, "x-axis") as motor:
        motor.events.position.connect(lambda new, old: print(old, "->", new))
        child = mp.Process(target=worker)
        child.start()
        child.join()                      # prints: 1 -> 10
    Motor.unlink()
```

## Reacting to changes

`box.events` is a psygnal `SignalGroup`: `box.events.position.connect(cb)`
calls `cb(new, old)` when any thread or process changes `position`, and
`box.events.connect(cb)` reports every field. Callbacks run on a background
thread; pass `thread="main"` to `connect` and call `psygnal.emit_queued()`
from your event loop to run them on the main thread.

To wait instead of reacting, `box.watch(field)` iterates over new values
with `for` or `async for`, skipping values written while the consumer was
busy.

## Lifetime

As with `multiprocessing.shared_memory.SharedMemory`: `close()` (or leaving
the `with` block) detaches one box and never destroys the data, and
`unlink()` removes the segment's name. Call `Motor.unlink()` once, usually
from the process that created the box. On Linux a segment that is never
unlinked stays in `/dev/shm` until reboot, and the next `Motor(...)` then
raises `SegmentExistsError`; on Windows the OS frees it when the last box
closes and `unlink()` does nothing. sharedbox does not unlink anything at
exit, like `SharedMemory(track=False)`.

## Limitations

- Field types: `bool`, `int` (64-bit), `float`, and `str` or `bytes` with a
  `Capacity` in bytes. Nested boxes and arrays are not supported yet.
- Writers take one lock per box. Readers never block writers.
- macOS is not supported.

The full API is described in [docs/api.md](./docs/api.md).

## C++ and C

The segment layout is implemented by a header-only C++20 library,
`sharedbox.hpp`, installed with the wheel together with a minimal C
interface, `sharedbox_c.h`. C++ and C code can use a box through them,
either as an extension that takes a box from Python or as a standalone
program; [docs/library-authors.md](./docs/library-authors.md) shows how.

## Wheels

Each platform gets one wheel for CPython 3.11, one `abi3` wheel for CPython
3.12 and newer, and one for free-threaded CPython 3.14. Wheels are built for
Windows x64 and Linux x86_64 (glibc and musl).

## Building locally

### Requirements

- [`git`](https://git-scm.com/downloads)
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Python >= 3.11
- [`CMake`](https://cmake.org/download/) >= 3.30
- A C++20 compiler (MSVC on Windows, GCC on Linux)

### Build the package

```bash
git clone https://github.com/jacopoabramo/sharedbox.git
cd sharedbox
uv sync --dev
```

`uv sync --dev` creates `.venv`, builds the extension and installs it.

### Development setup

After `uv sync --dev`, run this once to point VS Code's C/C++ extension at
the CPython, nanobind and sharedbox headers the build uses:

```bash
uv run python scripts/vscode_setup.py
```

Run it again after changing the Python version or deleting `build/`.

Run `uv run prek install` once to lint and format each commit; `uv run tox
-e lint` runs the same checks on demand.

### Running tests

```bash
uv run pytest               # current interpreter
uv run tox                  # every supported Python version, plus mypy
uv run tox -e py314t        # one version
uv run pytest -m stress     # stress tests, deselected by default (about 3 minutes)
```

The C++ tests of the header build with CMake:

```bash
cmake -S tests/cpp -B build-cpp -DCMAKE_BUILD_TYPE=Release
cmake --build build-cpp --config Release
ctest --test-dir build-cpp -C Release --output-on-failure
```

### Running benchmarks

The `benchmarks` extra installs a `benchbox` command that measures
`sharedbox` on your own machine:

```bash
pip install "sharedbox[benchmarks]"

benchbox ops               # single operations, against the standard library
benchbox ops --fast --filter "read*" --json ops.json
benchbox roundtrip         # change notification between two processes
benchbox size dist/*.whl   # wheel and extension module size
benchbox all --out results # all of the above, plus results/summary.md
```

`ops` runs on [pyperf](https://pyperf.readthedocs.io); arguments after `--`
are passed to it unchanged. `all` writes each command's JSON output and a
Markdown summary with the OS, CPU, Python version and build, and the
`sharedbox` version. It measures wheel sizes only when it finds wheels in
`dist/` or `wheelhouse/`, and then measures every wheel there, older builds
included. `python -m sharedbox.benchmarks` runs the same command.

In a checkout, `uv sync` installs the `benchmarks` dependency group, which
has the same packages as the extra, so
`uv run benchbox` works there too. The pytest benchmarks are separate
and live only in the repository:

```bash
uv run pytest benchmarks --codspeed
```

CI runs them on CodSpeed for every push and pull request to `main`.

## License

Licensed under [Apache 2.0](./LICENSE)
