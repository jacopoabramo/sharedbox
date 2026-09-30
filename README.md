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

## Documentation

The [documentation site](https://jacopoabramo.github.io/sharedbox) has a
tutorial, how-to guides, explanations of how a box works, and the API
reference.

C++ and C code can take a box from Python or open one by name; see the
[C and C++ guides](https://jacopoabramo.github.io/sharedbox/how-to/accept-a-box-in-cpp/).

## License

Licensed under [Apache 2.0](./LICENSE)
