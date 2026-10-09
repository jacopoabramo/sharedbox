# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Dates are marked as `DD-MM-YYYY`

## [Unreleased]

### Changed

- Segment names: one or more segments of `[A-Za-z0-9_-]` joined by `:`, up
  to 240 characters, such as `bl01:camera:det1:frames`. `.` is no longer
  allowed. The shared memory is `/dev/shm/SBX:<name>` on Linux and
  `Local\SBX:<name>` on Windows, and Windows waiter events end in `#w<i>`.
- `box_ref`: a reference field stores the name of its target in 240 bytes,
  so the field takes 256 bytes.
- Segment layout: every segment starts with a common first line (magic,
  core version, layout version of its kind, schema hash, creator), and
  boxes use layout 3.0. A box made by 0.5 cannot be opened by 0.6, nor the
  other way round.
- `sharedbox.hpp`: split into `core.hpp` and `box.hpp`, which it includes.
  `sharedbox::result` is the library's own type on C++20 and C++23 alike,
  and its `error()` is a `sharedbox::error` holding the status, the OS error
  and what was found. The C++ names moved from `sharedbox::v2` to
  `sharedbox::v3`.

### Added

- `KindMismatchError`, a `SchemaMismatchError` raised when a name holds a
  segment of another kind; `status::kind_mismatch` and `SBX_E_KIND` (-11)
  in `sharedbox.hpp` and `sharedbox_c.h`.
- `status::foreign` and `SBX_E_FOREIGN` (-12): the segment's magic is not
  one this version knows. Python raises `SchemaMismatchError`.
- `sharedbox::to_expected`: converts a `sharedbox::result` to a
  `std::expected` on C++23.

### Removed

- Reading box layouts 1.0 and 2.0.

## [0.5.0] - 09-10-2026

### Added

- `SharedBox.read_into`: copies an array field into an array the caller
  passes and returns it, without allocating a new array.
- `SharedBox.writing`: a context manager that holds the box's write lock
  and yields an array field as an array to fill in place.
- `benchbox contention`: times writes and reads of an `int` while several
  writer and reader processes share it, for a box, `mp.Value` and
  `SharedMemory` with a `Lock`.

### Changed

- `SharedBox`: a field named `read_into` raises `TypeError`.
- `SharedBox`: a field named `writing` raises `TypeError`.
- `SharedBox`: reading a collection, tuple or record field whose members
  are `bool`, `int`, `float` or `str` takes less time: about 90 ns less
  for a list of 16 `float` on Windows.
- `SharedBox`: reading a field whose value cannot be changed (a frozen
  dataclass, attrs class or msgspec Struct, a NamedTuple, a tuple, a
  frozenset, an enum, flag or literal, `complex`, `date`, `time`,
  `datetime`, `timedelta`, `UUID` or `Decimal`) returns the value the
  previous read built until the field is written: 40 ns instead of 540 ns
  for a frozen dataclass of four fields on Windows. A tuple, frozenset or
  record qualifies only when its members are of these kinds or `bool`,
  `int`, `float`, `str` or `bytes`.
- `SharedBox`: a frozen record's `__post_init__`, attrs converters and
  validators run on the first read after each write instead of on every
  read.

### Fixed

- `SharedBox.attach`: a box whose header gives a field count other than the
  class's raises `SchemaMismatchError` instead of `ValueError`.

## [0.4.1] - 07-10-2026

### Changed

- `SharedBox.update`: takes about 7 to 9 ns less per call on Windows.

### Fixed

- `SharedBox.events`, `SharedBox.watch` and `BoxEvents.follow`: a process
  that exits while watcher threads still run no longer aborts with
  `terminate called without an active exception` on Linux with CPython
  3.11 to 3.13. At exit, sharedbox stops every watcher thread and waits
  up to 2 seconds in all for them to end, including any callback they are
  running.

## [0.4.0] - 05-10-2026

### Added

- `benchbox plot`: draws the results of `benchbox all` as SVG charts, in a
  light and a dark variant. The `benchmarks` extra now includes
  matplotlib.
- `SharedBox`: fields of type `complex`, `datetime.date`, `datetime.time`,
  `datetime.datetime`, `datetime.timedelta`, `uuid.UUID` and
  `Annotated[decimal.Decimal, Capacity(n)]`.
- `SharedBox`: `enum.Enum`, `enum.Flag` and `typing.Literal` fields.
- `SharedBox`: `X | None` and union fields.
- `SharedBox`: record fields: dataclasses, `NamedTuple`, fixed-length
  tuples, `TypedDict`, attrs classes and `msgspec.Struct`.
- `SharedBox`: `list`, `tuple[T, ...]`, `set`, `frozenset`, `dict` and
  `bytearray` fields and their `collections.abc` forms, with a `Capacity`
  in elements.
- `Shape`, `DType`, `SupportsDLPack`, `register_array_type`: array fields
  through DLPack.
- `sharedbox.hpp`: `type_view`, `handle::field_type`,
  `handle::types_table`, `handle::major_version`, `oldest_layout_major`,
  `time_value`, `datetime_value`, `timedelta_value`, the kind codes
  `kind_complex` to `kind_decimal` and `kind_enum` to `kind_array`,
  `max_type_depth`, `max_types_size`, `max_mapping_size`,
  `first_described_kind`, `max_date_ordinal`, `unix_epoch_ordinal`,
  `micros_per_day`, `max_offset_minutes`, `literal_none` to
  `literal_enum`, `type_head`, `dl_dtype`, `literal_value`,
  `encode_*` and `decode_*` functions for complex, date, time, datetime,
  timedelta, uuid, flag and position values, `decode_bool`,
  `decode_present`, `decode_tag`, `decode_length`, `handle::read_used`,
  `handle::read_large`, `handle::read_record_large` and
  `handle::write_large`.
- `sharedbox_c.h`: `sbx_field_desc`, the structs `sbx_time`,
  `sbx_datetime` and `sbx_timedelta`, and typed reads and writes of
  complex, date, time, datetime, timedelta, UUID, enum and literal
  positions, flag bits and optional presence.
- `benchbox ops`: rows for datetime, record, list and array fields.

```python
class Frame(SharedBox):
    taken: datetime.datetime
    image: Annotated[np.ndarray, Shape(480, 640), DType("uint8")]
    tags: Annotated[list[Annotated[str, Capacity(16)]], Capacity(8)] = field(default_factory=list)
```

### Changed

- Segments use layout 2.0. Releases 0.3.0 and 0.3.1 cannot open a box made
  by this version; this version opens boxes made by them.
- `SharedBox.events` and `SharedBox.watch()`: a field counts as changed
  when its stored bytes change. Writing `NaN` again no longer emits;
  `-0.0` after `0.0` does.
- `Capacity`: also sets the most elements of a collection.
- `sharedbox.hpp`: the inline namespace is `v2`; `handle::create` and
  `handle::create_unpublished` take a description table.
- The `benchmarks` extra includes numpy.
- `SharedBox.update` and `SharedBox.snapshot`: native methods, unless a
  class defines or inherits its own.

### Fixed

- `SharedBox`: a class with more than one box base keeps the defaults and
  default factories of the fields it inherits from every base, not only
  the first.

## [0.3.1] - 01-10-2026

### Added

- `sharedbox.__version__`: the version of the installed package, as a string.

## [0.3.0] - 01-10-2026

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

- Documentation site at <https://jacopoabramo.github.io/sharedbox>:
  tutorial, how-to guides, explanations and the API reference.

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
- `docs/api.md`, `docs/library-authors.md` and `docs/design/`: their
  content is on the documentation site.

## [0.2.4] - 05-10-2025

### Changed

- Rewrite codebase in nanobind

### Fixed

- Parallelize CI so that each wheel is built with the correct version
  - Also faster builds

## [0.1.0] - 29-09-2025

### Added

- Initial release

[Unreleased]: https://github.com/jacopoabramo/sharedbox/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/jacopoabramo/sharedbox/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/jacopoabramo/sharedbox/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/jacopoabramo/sharedbox/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/jacopoabramo/sharedbox/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/jacopoabramo/sharedbox/compare/0.2.4...v0.3.0
[0.2.4]: https://github.com/jacopoabramo/sharedbox/compare/0.1.0...0.2.4
[0.1.0]: https://github.com/jacopoabramo/sharedbox/commits/0.1.0
