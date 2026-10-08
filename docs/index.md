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

`sharedbox` lets several Python processes share one record, as if they all
held the same dataclass. You declare the fields once, and every process that
opens the record reads and writes the same values. The values sit in shared
memory, a block of memory the operating system lets several processes use
at once, so no process has to send them to another. Such a record is a
[box](explanation/glossary.md#box).

## A first look

Here a parent process creates a box, starts a child that moves the motor,
and prints the change the moment it happens:

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

`Capacity(32)` gives the text field room for 32 bytes, since every field
has a fixed size. Running the script prints:

```text
1 -> 10
```

Step through what happens, and point at a shape to read more:

```d2 title="One box, two processes"
...@diagrams/style
direction: left
parent: "parent process" {
  class: process
  tooltip: Creates the box with Motor(1, False, "x-axis") and connects a callback to events.position.
}
box: "the box\nin shared memory" {
  class: hardware
  tooltip: One named block of shared memory holding position, enabled and label. Every process that opens it reads and writes the same bytes.
}
child: "child process" {class: [process; hidden]}
parent -> box: "creates"
child -> box: "attaches by class" {style.opacity: 0}
child -> box: "position = 10" {style.opacity: 0}
box -> parent: "prints 1 -> 10" {style.opacity: 0}
steps: {
  1: {
    child.class: process
    child.tooltip: Runs worker(). It is never handed the box. Motor.attach() works out the box's name from the class.
    (child -> box)[0].style.opacity: 1
  }
  2: {
    (child -> box)[1].style.opacity: 1
  }
  3: {
    (box -> parent)[0].style.opacity: 1
    parent.tooltip: The parent's watcher thread wakes on the write and calls the callback with the new and old value.
  }
}
```

## Is it for you?

`sharedbox` fits when separate processes need the same small set of values,
such as the state of a device that one process controls and another shows.
Reading or writing a number or a short string takes well under a
microsecond, and a process that is reading never holds up one that is
writing.

If your code runs in threads of one process instead, you can share ordinary
Python objects, which is simpler;
[When to use sharedbox](explanation/when-to-use-sharedbox.md) compares the
two.

`sharedbox` runs on CPython 3.11 or newer, on Windows and Linux;
[How to install sharedbox](how-to/install-sharedbox.md) has the details.

## Where to go next

- [Tutorials](tutorials/index.md): start here if `sharedbox` is new to you,
  and build a small script one step at a time.
- [How-to Guides](how-to/index.md): install `sharedbox`, then get one task
  done, such as storing an array or naming a box.
- [Explanations](explanation/index.md): how a box works inside, and why it
  is built that way.
- [Reference](reference/index.md): the API, the memory layout and the C and
  C++ interface.
- [Changelog](reference/changelog.md): what changed in each release.
- [Contributing](how-to/contribute.md): set up a copy you can change, run
  the tests and send your change.
