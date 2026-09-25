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

### Changed

- Building the extension requires nanobind 3.1.0 or newer.
- Wheels per platform: `cp311-cp311`, `cp312-abi3` for CPython 3.12 and
  newer, and `cp314-cp314t` for free-threaded CPython 3.14.
- Boost is installed through a vcpkg manifest pinned to one baseline.
- `SharedBox` fields are converted in the native module and packed by
  alignment; segments use layout version 3.

### Removed

- Python 3.10 support.
- `SharedDict` and `sharedbox.utils`.

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
