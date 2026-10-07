# `sharedbox`

[![PyPI](https://img.shields.io/pypi/v/sharedbox)](https://pypi.org/project/sharedbox/)
[![CI](https://github.com/jacopoabramo/sharedbox/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/jacopoabramo/sharedbox/actions/workflows/ci.yaml)
[![CodSpeed](https://img.shields.io/endpoint?url=https://codspeed.io/badge.json)](https://app.codspeed.io/jacopoabramo/sharedbox?utm_source=badge)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![clang-format](https://img.shields.io/badge/C%2B%2B%20style-clang--format-blue)](https://clang.llvm.org/docs/ClangFormat.html)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)

> [!WARNING]
> This project is a work in progress; be patient or feel free to contribute.

`sharedbox` lets several Python processes share one record, as if they all
held the same dataclass. You declare the fields once, and every process that
opens the record reads and writes the same values in shared memory, so no
process has to send them to another.

## Installation

`sharedbox` needs CPython 3.11 or newer on Windows x64 or Linux x86_64. PyPI
has compiled wheels, so you don't need a compiler:

```sh
pip install sharedbox
```

or, in a project that [`uv`](https://docs.astral.sh/uv/) manages:

```sh
uv add sharedbox
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

The child finds the box by its class alone, and the parent prints the change
as soon as the child makes it.

## Documentation

The [documentation site](https://jacopoabramo.github.io/sharedbox) starts
with three tutorials, then has how-to guides, explanations of how a
box works, and the API reference. C and C++ code can use a box too, either
handed over from Python or opened by name; the
[C and C++ guides](https://jacopoabramo.github.io/sharedbox/how-to/accept-a-box-in-cpp/)
show how.

## Development

To change `sharedbox` itself you need `git`, `uv`, CMake 3.30 or newer and a
C++20 compiler (MSVC or GCC):

```sh
git clone https://github.com/jacopoabramo/sharedbox.git
cd sharedbox
uv sync --dev
uv run pytest
```

[How to set up a development environment](https://jacopoabramo.github.io/sharedbox/how-to/set-up-development/)
has the details.

## License

Licensed under [Apache 2.0](./LICENSE)
