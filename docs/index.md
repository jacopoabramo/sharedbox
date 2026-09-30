---
icon: lucide/house
hide:
  - toc
---

[![PyPI](https://img.shields.io/pypi/v/sharedbox.svg?color=green)](https://pypi.org/project/sharedbox)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/sharedbox)](https://pypi.org/project/sharedbox)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![CI](https://github.com/jacopoabramo/sharedbox/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/jacopoabramo/sharedbox/actions/workflows/ci.yaml)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-%23FE5196?logo=conventionalcommits&logoColor=white)](https://www.conventionalcommits.org)

# `sharedbox`

`sharedbox` keeps records in shared memory. Each [box](explanation/glossary.md#box) is one named [segment](explanation/glossary.md#segment), and every process that opens it reads and writes the same [fields](explanation/glossary.md#field).

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

Expected output:

```text
1 -> 10
```

## Where to go next

- [Tutorials](tutorials/index.md): build a first script, one step at a time
- [How-to Guides](how-to/index.md): install sharedbox and get one task done with a box
- [Explanations](explanation/index.md): how a box works and why it is built that way
- [Reference](reference/index.md): the API, the segment layout and the C and C++ interface
- [API reference](reference/api/index.md): every name `sharedbox` exports
- [Changelog](reference/changelog.md): what changed in each release
- [Contributing](how-to/contribute.md): set up a clone, run the tests and send a change
