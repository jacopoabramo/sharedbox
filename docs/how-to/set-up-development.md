---
icon: lucide/wrench
---

# How to set up a development environment

Get a copy of sharedbox you can change, build and test.

## Before you start

!!! note "What you need"

    - [`git`](https://git-scm.com/downloads)
    - [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
    - Python 3.11 or newer
    - [CMake](https://cmake.org/download/) 3.30 or newer
    - A C++20 compiler: MSVC on Windows, GCC on Linux

    macOS is not supported.

## Clone and build

```bash
git clone https://github.com/jacopoabramo/sharedbox.git
cd sharedbox
uv sync --dev
```

`uv sync --dev` creates `.venv`, builds the extension with CMake and installs
it. The build uses [scikit-build-core](https://scikit-build-core.readthedocs.io)
and [nanobind](https://nanobind.readthedocs.io), which `uv` fetches by
itself. Each Python version builds into a folder of its own named after
its wheel, such as `build/cp311-cp311-win_amd64`.

`uv sync` builds the extension again when a C++ source, a header, a file in
`cmake/` or `CMakeLists.txt` has changed since the last build. Run it after
each such change, before running the tests.

The version comes from the git tags, through
[setuptools-scm](https://setuptools-scm.readthedocs.io), and is written to
`src/sharedbox/_version.py`, which git ignores.

## Point VS Code at the headers

If you use VS Code's C/C++ extension, run this once after `uv sync --dev`,
so it finds the CPython, nanobind and sharedbox headers the build uses:

```bash
uv run python scripts/vscode_setup.py
```

Run it again after changing the Python version or deleting `build/`.

## Install the git hook

Once per clone, so formatting and lint run on every commit:

```bash
uv run prek install
```

See [How to run the commit checks](run-commit-checks.md) for what the hook
does.

## Check that it works

```bash
uv run tox
```

This runs the tests on every supported Python version, the type checks, the
lint and the docs build, each in its own environment built from `uv.lock`.
[How to run the tests](run-tests.md) explains each environment.
