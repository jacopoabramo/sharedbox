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
  `sharedbox/sharedbox_c.h` and `sharedbox/sharedbox_c.cpp`.
- `sharedbox.hpp`: header-only C++20 implementation of the segment layout,
  installed in the wheel with a CMake config (`sharedbox::headers`), which
  links `bcrypt` on Windows and `rt` and `Threads::Threads` on Linux.
- `sharedbox-config-version.cmake`: installed next to
  `sharedbox-config.cmake`; `find_package(sharedbox 0.3)` accepts 0.3
  versions only.
- `sharedbox_c.h`: minimal C interface (`sbx_open`, `sbx_import`,
  `sbx_schema_hash`, `sbx_read`, `sbx_write`, `sbx_release`), built
  through `sharedbox::c`.

```python
class Frame(SharedBox, identity="camera/frame/1", max_waiters=16):
    exposure: float = 0.01
    count: int = 0
```

- `field()`: per-field `default`, `default_factory`, `init`, `repr`,
  `kw_only`, `metadata` and `doc` for `SharedBox` fields.
- `fields()`: the `Field` of each field of a `SharedBox` subclass or box.
- `Field`: read-only description of one `SharedBox` field.
- `SharedBox`: `dataclasses.InitVar` annotations, passed to
  `__post_init__` and not stored.
- `SharedBox.__post_init__()`: runs after a box is created and before
  other processes can attach to it.
- `SharedBox`: `inspect.signature()` of a subclass gives its constructor
  parameters.
- `sharedbox.hpp`: `handle::create_unpublished()` and
  `handle::publish()`.
- `SharedBox`: reference fields, annotated with a `SharedBox` subclass `X`
  or `X | None`; reading one attaches the box it refers to, with that
  box's own class.
- `BoxRef`: frozen dataclass (`name`, `schema_hash`, `create_id`, and the
  `box_class` property) that `snapshot()`, `events` and `watch()` report
  for a reference field.
- `BrokenReferenceError`: raised when the box a reference field refers to
  was removed or created again since it was assigned.
- `UnknownBoxClassError`: raised when the box a reference field refers to
  has a class this process has not defined.
- `SharedBox.snapshot()`: `follow` keyword; `follow=True` replaces each
  reference with the snapshot of the box it refers to.
- `sharedbox.hpp`: `kind_ref` and `box_ref`, the kind code and stored value
  of a reference field.

```python
class Motor(SharedBox):
    position: int
    limit: int = field(default=100, kw_only=True, metadata={"unit": "mm"})
    offset: InitVar[int] = 0

    def __post_init__(self, offset: int) -> None:
        self.position += offset


class Stage(SharedBox):
    motor: Motor | None = None
```

- `BoxEvents`: psygnal `SignalGroup` subclass that `SharedBox.events`
  returns.
- `BoxEvents.follow()`: given a reference field, returns a group whose
  signals are emitted for changes inside the box the field refers to,
  whichever box that is; given no field, emits on `nested` every change
  inside the boxes the reference fields reach.
- `BoxEvents.unfollow()`: stops forwarding started with `follow()`.
- `BoxEvents.nested`: `(path, new, old)` signal of a class with reference
  fields.

```python
motor_events = stage.events.follow("motor")
motor_events.position.connect(lambda new, old: print(new))
stage.events.follow()
stage.events.nested.connect(lambda path, new, old: print(path, new))
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
- `SharedBox.update()`: takes about half the time; field names are checked
  only when one is unknown.
- `SharedBox.snapshot()`: builds its dict in the native module.
- `SharedBox`: the `kw_only` class keyword and a `KW_ONLY` annotation
  apply only to the fields of the class that declares them; a subclass
  keeps each inherited field's keyword-only setting.
- `SharedBox`: a subclass that sets an unannotated class attribute on the
  name of an inherited field raises `TypeError`.
- `SegmentExistsError`: for a box whose creator is still running
  `__post_init__`, says the box is being created by that pid.
- `SharedBox.__sharedbox_box__()`: raises `BufferError` before the box
  is published.
- `sharedbox.hpp`: `handle::open` and `handle::from_capsule` accept a field
  whose kind code they do not know and treat its bytes as opaque.
- `sharedbox.hpp`: `handle::write` returns `status::range` for a field whose
  kind code it does not know, and `sbx_write` returns `SBX_E_RANGE`.
- `SharedBox.attach()`: a segment with a field of a kind this version
  cannot read raises `SchemaMismatchError` naming the kind.
- `SharedBox`: an annotation naming an undefined class raises `TypeError`.
- `SharedBox`: a field named `follow`, `unfollow` or `nested` raises
  `TypeError`.
- `SharedBox.close()`: called from an event callback, on a watcher thread,
  does not wait for the watcher threads of the boxes it closes, and drops
  the writes they had not delivered.

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
