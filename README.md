# `sharedbox`

[![CodSpeed](https://img.shields.io/endpoint?url=https://codspeed.io/badge.json)](https://app.codspeed.io/jacopoabramo/sharedbox?utm_source=badge)

> [!WARNING]
> This project is a work in progress; be patient or feel free to contribute.

`sharedbox` keeps records in shared memory. Each box is one named segment, made with the [`boost::interprocess`](https://www.boost.org/doc/libs/latest/doc/html/interprocess.html) library, and every process that opens it reads and writes the same fields.

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

## Wheels

Each platform gets one wheel for CPython 3.11, one `abi3` wheel for CPython
3.12 and newer, and one for free-threaded CPython 3.14. Wheels are built for
Windows x64 and Linux x86_64 (glibc and musl).

## Building locally

### Requirements

- [`git`](https://git-scm.com/downloads)
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Python >= 3.11
- [`vcpkg`](https://vcpkg.io/en/)
- [`CMake`](https://cmake.org/download/) >= 3.30
- A C++17 compatible compiler (MSVC on Windows, GCC on Linux)

> [!NOTE]
> CMake finds `vcpkg` through the `VCPKG_ROOT` environment variable and stops if it is not set.

### Install and configure vcpkg

[Install and bootstrap vcpkg](https://learn.microsoft.com/en-us/vcpkg/get_started/get-started?pivots=shell-cmd) somewhere on your system.

#### Windows
```cmd
cd C:\
git clone https://github.com/microsoft/vcpkg.git
cd vcpkg
bootstrap-vcpkg.bat

# Set the VCPKG_ROOT environment variable (permanently)
setx VCPKG_ROOT "C:\vcpkg"
```

#### Linux
```bash
git clone https://github.com/microsoft/vcpkg.git ~/vcpkg
~/vcpkg/bootstrap-vcpkg.sh
export VCPKG_ROOT=~/vcpkg   # add this line to your shell profile
```

Boost is listed in `vcpkg.json` and installed by CMake on the first build.

### Build the package

```bash
git clone https://github.com/jacopoabramo/sharedbox.git
cd sharedbox
uv sync --dev
```

`uv sync --dev` creates `.venv`, builds the extension and installs it.

### Development setup

After `uv sync --dev`, run this once to point VS Code's C/C++ extension at
the CPython, nanobind and Boost headers the build uses:

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
```

### Running benchmarks

```bash
uv run python benchmarks/bench_ops.py        # single operations, against the standard library
uv run python benchmarks/bench_roundtrip.py  # change notification between two processes
uv run pytest benchmarks --codspeed          # the benchmarks CI runs
```

CI runs the pytest benchmarks on CodSpeed for every push and pull request to `main`.

## License

Licensed under [Apache 2.0](./LICENSE)

`sharedbox` is built using the Boost C++ library, which is licensed under the [Boost Software License](https://boost.org.cpp.al/LICENSE_1_0.txt).
