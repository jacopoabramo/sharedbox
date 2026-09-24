# CLAUDE.md

`sharedbox` gives Python processes a record that lives in shared memory.
`SharedBox` is a base class: a subclass's annotated fields are stored in one
named segment that every process can open. The segment is C++
(Boost.Interprocess) exposed to Python with nanobind; the Python side decides
the layout and encodes values.

## Repository layout

```text
sharedbox/
|-- src/sharedbox/
|   |-- __init__.py            re-exports the public API
|   |-- _box.py                SharedBox: class keywords, fields, create/attach, update, snapshot, unlink
|   |-- _layout.py             Capacity, field offsets and encoding, schema hash
|   |-- _events.py             FieldWatch, the watcher thread, psygnal events
|   |-- _native.pyi            hand-written stub for the extension
|   |-- py.typed
|   `-- _native/
|       |-- module.cpp         nanobind module: Segment and the error classes
|       |-- segment.{hpp,cpp}  the segment: header, record, sequence lock
|       `-- notifier.{hpp,cpp} wakes waiters across processes (futex, named semaphore)
|-- tests/                     pytest; many tests spawn processes
|   `-- type_checks/           checked by mypy, never imported at run time
|-- benchmarks/bench_box.py    latency benchmarks, not tests
|-- scripts/vscode_setup.py    points VS Code's C/C++ extension at the build headers
|-- docs/api.md                API reference
|-- docs/design/               design notes for the native segment
|-- .github/workflows/ci.yaml  cibuildwheel wheels, tests, PyPI publish
|-- CMakeLists.txt             extension build
|-- vcpkg.json                 Boost dependency and vcpkg baseline
|-- stubtest-allowlist.txt     stubtest exceptions for nanobind types
|-- pyproject.toml             scikit-build-core, setuptools-scm, pytest, cibuildwheel, mypy, tox
`-- uv.lock
```

- `_native.pyi` is written by hand. Update it whenever the bound API changes;
  the tox `mypy` env runs stubtest against it.

## Memory layout

The design and its reasons are in `docs/design/native-segment.md`.

One segment per box: `managed_windows_shared_memory` on Windows (page-file
backed), `managed_shared_memory` on Linux (`/dev/shm`, mode `0600`). The
name is the `name` class keyword, the name passed to `create()`, or by
default `sharedbox-` plus 16 hex digits of SHA-256 over the class's
`module.qualname` (`__mp_main__` counts as `__main__`). Creating always asks
for a new name and raises `SegmentExistsError` if it is taken.

The segment holds a named object `"sharedbox.header"` and the record,
allocated 64-byte aligned.

`Header` fields:

- `magic`: written last on create; `attach()` waits for it.
- `abi_version`: `1`; any other value is refused.
- `field_count`, `record_size`.
- `schema_hash`: first 8 bytes of SHA-256 over the class identity and each
  field's `name:kind:capacity`. `attach()` raises `SchemaMismatchError` if
  it differs.
- `record`: offset of the record in the segment.
- `seq`: sequence lock. Even when free, odd while a write runs. Readers copy
  and retry if `seq` moved; writers take it with a compare-and-swap and give
  up after `lock_timeout` with `LockTimeoutError`.
- `generation`: counts every write. `wake_word` and `waiters` wake threads
  that wait for a change.
- `writer_pid`: process holding the write lock, cleared by a normal unlock.
  `force_unlock()` releases the lock and leaves `writer_pid` as it is.
- `fields[256]`: `offset`, `capacity`, `kind` (0 fixed, 1 prefixed) per field.
  `attach()` checks each one and keeps its own copy.
- `versions[256]`: write count per field.

### Record encoding

Fields in declaration order, base classes first, each starting at a multiple
of 8 bytes. Everything is little-endian.

- `bool`: 1 byte, `0x00` or `0x01`.
- `int`: 8 bytes, signed.
- `float`: 8 bytes, IEEE 754 double.
- `str`, `bytes`: `u32` length, then up to `capacity` bytes (UTF-8 for `str`).

Nothing is pickled. At most 256 fields; a capacity is 1 byte to 1 MiB.

### Lifecycle

- `close()` stops the watcher thread and detaches this box. Later reads and
  writes raise `BoxClosedError`. Garbage collection closes a box too.
- `unlink()` removes the name on Linux and does nothing on Windows, where the
  OS frees the segment with its last handle.
- A Linux segment that is never unlinked stays in `/dev/shm`. Most tests use the
  `unique_name` fixture, which removes the file afterwards.

## Build

scikit-build-core drives CMake (3.30 or newer), which needs:

- `VCPKG_ROOT` set; CMake stops without it.
- Boost from `vcpkg.json`, installed by CMake on the first build.
- nanobind (build dependency) and a C++17 compiler (MSVC or GCC). macOS is
  not supported.

The version comes from git tags through setuptools-scm, written to
`src/sharedbox/_version.py` (ignored by git).

```sh
uv sync --dev                          # creates .venv, builds and installs the extension
uv run python scripts/vscode_setup.py  # once, for VS Code's C/C++ extension
```

`[tool.uv] cache-keys` lists the C++ sources, so `uv sync` rebuilds the
extension after they change. Build folders are `build/<wheel tag>`.

Wheels per platform (Windows x64, Linux x86_64 glibc and musl): `cp311-cp311`,
`cp312-abi3` for CPython 3.12 and newer, and `cp314-cp314t` for free-threaded
CPython 3.14. One `nanobind_add_module(... STABLE_ABI FREE_THREADED ...)` line
produces all three.

## Tests

```sh
uv run pytest                          # current interpreter
uv run pytest tests/test_box.py -k pickle
uv run tox                             # py311 to py314, py314t, mypy
uv run tox -e py314t                   # one env
```

CI (`.github/workflows/ci.yaml`) builds the wheels above with cibuildwheel,
runs pytest against each wheel, and publishes to PyPI on a GitHub release.
Docker runs with `--shm-size=1g`, so keep test segments under that.

## Usage

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

Every read decodes a fresh value from the segment. `update(**values)` writes
several fields at once; `watch(field)` and `events` report changes from any
process.

## Conventions

- Changelog: `CHANGELOG.md`, Keep a Changelog format, dates as `DD-MM-YYYY`.
- Lint and format Python with `ruff` (dev dependency).
- Change dependencies with `uv add` / `uv remove`, never by editing
  `pyproject.toml`.
