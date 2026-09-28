# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Dates are marked as `DD-MM-YYYY`

## [0.3.0] - Unreleased

### Added

- `SharedBox`: base class whose annotated fields live in a shared-memory segment.
- `Capacity`: byte capacity for `str` and `bytes` fields.
- `FieldWatch`: `for` and `async for` over new values of a field.
- `SharedBox.events`: psygnal `SignalGroup` with one `(new, old)` signal per
  field.
- `BoxClosedError`, `LockTimeoutError`, `SchemaMismatchError`,
  `SegmentExistsError`, `SegmentNotFoundError`.
- `benchbox`: command with `ops`, `roundtrip`, `size` and `all`
  benchmarks, also run as `python -m sharedbox.benchmarks`.
- `benchmarks` extra: installs Typer and pyperf for `benchbox`.
- `SharedBox`: `identity` class keyword; it enters the schema hash and
  names the box.
- `SharedBox`: `max_waiters` class keyword, 1 to 4096 box handles that
  watch at once, default 64.
- `SharedBox.__sharedbox_box__()`: returns a `"sharedbox_box"` PyCapsule
  for other extensions.
- `SupportsSharedBox`: protocol for functions that accept a box.
- `get_include()`: folder holding `sharedbox/sharedbox.hpp`,
  `sharedbox_c.h` and `sharedbox_c.cpp`.
- `sharedbox.hpp`: header-only C++20 implementation of the segment layout,
  installed in the wheel with a CMake config (`sharedbox::headers`).
- `sharedbox_c.h`: minimal C interface (`sbx_open`, `sbx_import`,
  `sbx_schema_hash`, `sbx_read`, `sbx_write`, `sbx_release`), built
  through `sharedbox::c`; it may be removed in a future major version.

```python
class Frame(SharedBox, identity="camera/frame/1", max_waiters=16):
    exposure: float = 0.01
    count: int = 0
```

### Changed

- Building the extension requires nanobind 3.1.0 or newer and a C++20
  compiler.
- Wheels per platform: `cp311-cp311`, `cp312-abi3` for CPython 3.12 and
  newer, and `cp314-cp314t` for free-threaded CPython 3.14.
- `SharedBox` fields are converted in the native module and packed by
  alignment; segments use layout 1.0, a plain named mapping.
- `SharedBox`: the default box name is 16 hex digits of SHA-256 over the
  identity, with no `sharedbox-` prefix. A box created by an earlier
  release under `sharedbox-<hash>` is not found under the new default name.
- Shared-memory objects are named `sharedbox.<name>`
  (`/dev/shm/sharedbox.<name>`, `Local\sharedbox.<name>`); boxes of
  earlier releases are not found under them.
- `SegmentExistsError`: says whether the box's creator is still running,
  runs in another pid namespace, or has exited, or that the name holds no
  published box.
- A pickled `SharedBox` carries its class's schema hash and its box's
  create id; unpickling with a different class, or after the box was
  created again, raises `SchemaMismatchError`.
- `SharedBox.attach()`: shared memory that does not become a box within
  1 s raises `SegmentNotFoundError`.
- `SharedBox.attach()`: a segment of another layout major version raises
  `SchemaMismatchError` naming the version.
- `SharedBox` fields: reading or writing one field runs no Python code;
  deleting one raises `AttributeError`.
- `SharedBox`: a subclass whose `__slots__` names `_segment` raises
  `TypeError`.
- `SharedBox`: an error about a field names the field as `Class.field`.
- `SharedBox`: a read or write that waits for another writer's lock lets
  other threads run.

### Fixed

- `SharedBox.watch()`: no longer yields the same value twice.
- `SharedBox.attach()`: no longer fails when it runs while another process
  is still creating the box.
- `SharedBox`: a process that attaches while the box is being created sees
  the initial values, never a zeroed record.
- `SharedBox.close()`: no longer deadlocks while another thread's read or
  write waits for the write lock.
- `SharedBox.close()`: returns without waiting for the watcher thread's
  0.1 s poll.
- `SharedBox.watch()` and `SharedBox.events`: on Windows, a write no
  longer reaches one of several waiting threads up to 50 ms late.
- `SharedBox.watch()` and `SharedBox.events`: keep delivering when every
  waiter slot is taken.
- `SharedBox`: on Linux, a box larger than the free space of `/dev/shm`
  raises `OSError` at creation instead of the process receiving `SIGBUS`.

### Removed

- Python 3.10 support.
- `SharedDict` and `sharedbox.utils`.
- Boost and vcpkg: the build no longer needs `VCPKG_ROOT`.

## [0.2.4] - 05-10-2025

### Changed

- Rewrite codebase in nanobind

### Fixed

- Parallelize CI so that each wheel is built with the correct version
  - Also faster builds

## [0.1.0] - 29-09-2025

### Added

- Initial release

[0.2.4]: (https://github.com/jacopoabramo/sharedbox/compare/0.1.0...0.2.4)
[0.1.0]: (https://github.com/jacopoabramo/sharedbox/commits/0.1.0)
