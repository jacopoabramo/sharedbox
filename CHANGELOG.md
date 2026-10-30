# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Dates are marked as `DD-MM-YYYY`

<!-- towncrier release notes start -->

## [0.8.0](https://github.com/jacopoabramo/sharedbox/releases/tag/v0.8.0) - 30-10-2026

### Breaking

- `sharedbox.Shape` takes its dimensions as one tuple, `Shape((480, 640))`, in
  place of one argument per dimension. See
  [How to migrate from 0.7](https://jacopoabramo.github.io/sharedbox/how-to/migrate-from-0.7/). ([#88](https://github.com/jacopoabramo/sharedbox/pull/88))
- `SharedBox` reserves the names `metadata`, `metadata_version` and
  `metadata_version_of`: a field or `InitVar` with one of them raises
  `TypeError` when the class is defined. Rename the field:

  ```python
  # 0.7
  class Parcel(SharedBox):
      metadata: Annotated[str, Capacity(64)] = ""

  # 0.8
  class Parcel(SharedBox):
      label: Annotated[str, Capacity(64)] = ""
  ```

  `SharedBox.update` takes the keyword `metadata`, so a type checker refuses
  `update(**values)` when the values of the dictionary have a type that the
  box's metadata type does not accept, such as `str`:

  ```python
  # 0.7
  def restock(parcel: Parcel, values: dict[str, str]) -> None:
      parcel.update(**values)

  # 0.8
  def restock(parcel: Parcel, values: dict[str, Any]) -> None:
      parcel.update(**values)
  ```

  See
  [How to migrate from 0.7](https://jacopoabramo.github.io/sharedbox/how-to/migrate-from-0.7/). ([#91](https://github.com/jacopoabramo/sharedbox/pull/91))

### Added

- `MaxShape`: an array of a stream item declared with a maximum shape, as in
  `Annotated[npt.NDArray[np.uint16], MaxShape((2048, 2048))]`, stores its own
  shape with each item, as kind 75 of stream layout 1.2. `writing()` views the
  slot in the shape of the template's array, `slot.send` publishes the first
  rows of that view without a copy, and `receive_into` returns a view of the
  stored shape over `out`. ([#88](https://github.com/jacopoabramo/sharedbox/pull/88))
- `SharedStream.sender(on_full="release")`: a send that has waited its whole
  `timeout` releases the lossless readers a full ring behind and sends, as in
  `stream.sender(on_full="release").send(item, timeout=0.1)`. Each released
  reader raises `ReaderReleased`, whose `missed` counts the items it lost, and
  goes on at the newest item; `ReceiveEvents.released` reports the count to
  callbacks. `SharedSendStream.asend` and `awriting` take a `timeout`. In
  `stream.hpp`, `send`, `send_with` and `begin` take an `on_full` policy, and a
  released reader's `receive` gives `status::released`, in stream layout 1.3. ([#89](https://github.com/jacopoabramo/sharedbox/pull/89))
- `MetadataFullError` and `MetadataGoneError`: raised when an add finds no
  room for a version, and when the version a read asks for is no longer held. ([#91](https://github.com/jacopoabramo/sharedbox/pull/91))
- `SharedStream.metadata`: a stream keeps metadata the same way.
  `SharedStream.create(Reading, capacity=8, metadata={"unit": "kg"})` stores
  version 1. `SharedSendStream.send`, `asend`, `send_nowait` and
  `SendSlot.send` take `metadata=` and add a version that applies from that
  item. `SharedReceiveStream.metadata` and `metadata_version` give the version
  of the item the reader received last. `SharedStream.create` takes
  `metadata_capacity` and `metadata_versions`, and `create` and `attach` take
  `metadata_type`. `sharedbox` 0.7.0 and earlier cannot open a stream created
  with metadata or a `metadata_capacity`. ([#91](https://github.com/jacopoabramo/sharedbox/pull/91))
- `box.hpp`, `stream.hpp` and `sharedbox_c.h`: box layout 3.2 and stream
  layout 1.4 hold metadata after the record or the description table.
  `handle` and `stream` gain `metadata_head` and `read_metadata`, and
  `stream_reader` gains `metadata_version_at`. `handle` and `stream_sender`
  gain `add_metadata`. `handle::write(values, doc, lock_timeout)` and
  `write_large` with the same arguments write fields and add a version in one
  write, and
  `stream_sender::can_add_metadata` checks an add without making it.
  `sharedbox_c.h` adds `sbx_metadata_head`, `sbx_read_metadata`,
  `sbx_add_metadata` and `SBX_E_METADATA_FULL`. ([#91](https://github.com/jacopoabramo/sharedbox/pull/91))
- `SharedBox.metadata`: a box keeps metadata, JSON-like values stored as CBOR
  in its segment, in numbered versions. `Scale.create(name, metadata={"unit":
  "kg"})` stores version 1, and `SharedBox.update(metadata=...)` adds the next
  one from any process, with or without field values, in one write.
  `metadata`, `metadata_version` and `metadata_version_of` read the versions,
  a map as a read-only mapping and a list as a tuple. `BoxEvents.metadata` is
  emitted with each new version. The class keywords `metadata_capacity` and
  `metadata_versions` size the room for versions, and
  `class Scale(SharedBox[Calibration])` gives the metadata's type to type
  checkers. ([#91](https://github.com/jacopoabramo/sharedbox/pull/91))

### Changed

- `sharedbox.DType` is a `StrEnum` with one member per element type:
  `DType(numpy.uint8)` returns `DType.uint8`, which equals `"uint8"`. ([#88](https://github.com/jacopoabramo/sharedbox/pull/88))

### Fixed

- `SharedBox` and `SharedStream` at exit: the `atexit` hook of `sharedbox`
  closes every box and stream the process still has open and releases the
  native objects of `SharedBox` subclasses, their watches and events groups,
  so nanobind reports no leaked instances when a box class defined at module
  level outlives shutdown. A box or stream used after the hook raises
  `BoxClosedError` or `StreamClosedError`, and creating or attaching a box
  raises `BoxClosedError`. ([#90](https://github.com/jacopoabramo/sharedbox/pull/90))


## [0.7.0](https://github.com/jacopoabramo/sharedbox/releases/tag/v0.7.0) - 28-10-2026

### Added

- `np.int8`, `np.int16`, `np.int32`, `np.uint8`, `np.uint16`, `np.uint32`,
  `np.uint64`, `np.float16`, `np.float32`, `np.complex64` and `np.datetime64`
  as field types, record members, collection elements and stream items,
  stored at their own width as kinds 13 to 23 of box layout 3.1 and stream
  layout 1.1. A field reads back as its numpy type, and a value that does not
  fit the width raises `OverflowError`. A `np.datetime64` field stores
  nanoseconds since 1970. ([#66](https://github.com/jacopoabramo/sharedbox/pull/66))
- `sbx_read_float16`, `sbx_write_float16`, `sbx_read_float32` and
  `sbx_write_float32` in `sharedbox_c.h`. ([#66](https://github.com/jacopoabramo/sharedbox/pull/66))
- An array annotated `X | None` as a field, record member or stream item,
  holding `None` or an array of its sizes, as in
  `scan: npt.NDArray[np.uint8] | None = field(default=None, shape=(32, 32))`.
  `receive_into` does not fill one. ([#67](https://github.com/jacopoabramo/sharedbox/pull/67))
- `field(capacity=...)`: the capacity of a box field of text, bytes,
  `bytearray`, `Decimal` or a collection, as in
  `samples: list[float] = field(capacity=16)`. ([#68](https://github.com/jacopoabramo/sharedbox/pull/68))
- An array member of a dataclass or attrs record takes its sizes from
  `metadata={"shape": (32, 32)}` on its field. ([#68](https://github.com/jacopoabramo/sharedbox/pull/68))
- `BoundedList[T]`: a list value that keeps the stored bytes of a list field,
  as in `samples: Annotated[BoundedList[float], Capacity(65536)]`. It takes
  every element type `list[T]` takes and is stored as `list[T]` is. A read
  copies the bytes without decoding an element, a write of a `BoundedList` of
  the field's element type copies them back, indexing decodes or encodes one
  element, and a change past the capacity raises `ValueError`. Subclasses,
  generic or with a fixed element type, can be declared in its place. ([#73](https://github.com/jacopoabramo/sharedbox/pull/73))
- `BoundedSet[T]`: a set value that keeps the stored bytes of a set field,
  as in `seen: Annotated[BoundedSet[int], Capacity(65536)]`. It is stored as
  `set[T]` is. A read copies the bytes without decoding an element, a write
  of a `BoundedSet` of the field's element type copies them back, and `in`,
  `add` and `remove` encode one key and compare stored bytes, through an
  index after 64 lookups in a set of more than 8 elements. The set operators take any set and return a
  `BoundedSet` of the same class and capacity, the comparisons take any set,
  and a change past the capacity raises `ValueError`. A declaration raises
  `TypeError` for an element type whose equal values could be stored
  differently, such as `Decimal` or `int | float`. Subclasses, generic or
  with a fixed element type, can be declared in its place. ([#77](https://github.com/jacopoabramo/sharedbox/pull/77))
- `BoundedDict[K, V]`: a dict value that keeps the stored bytes of a dict
  field, as in `weights: Annotated[BoundedDict[int, float], Capacity(4096)]`.
  It is stored as `dict[K, V]` is. A read copies the bytes without decoding
  an item, a write of a `BoundedDict` of the field's key and value types
  copies them back, and `d[key]`, `get` and `in` encode one key and compare
  stored bytes, through an index after 64 lookups in a dict of more than 8
  keys. It keeps insertion order, `|` takes any mapping and returns a
  `BoundedDict` of the same class and capacity, and a change past the
  capacity raises `ValueError`. Keys follow the rules of `BoundedSet`
  elements, and values can be of any type a `dict` field holds. Subclasses,
  generic or with fixed key and value types, can be declared in its place. ([#82](https://github.com/jacopoabramo/sharedbox/pull/82))
- `BoundedList` of `int`, `float`, `bool`, `complex` or NumPy integer, float,
  complex or `bool_` elements supports the buffer protocol: `np.asarray(value)`
  and `memoryview(value)` share its elements without decoding them, read only
  for `bool` and `bool_`, and a change of length raises `BufferError` while
  such a view exists. ([#85](https://github.com/jacopoabramo/sharedbox/pull/85))

### Changed

- Reading a `list`, `tuple`, `set`, `frozenset` or `dict` field allocates and
  copies less, and reading a record whose class takes every member by position
  no longer builds a dict of keyword arguments. ([#65](https://github.com/jacopoabramo/sharedbox/pull/65))
- A union stores a number through its first member of the same kind of
  number, so `np.int64(5)` written to `float | int` reads back as `5`. ([#66](https://github.com/jacopoabramo/sharedbox/pull/66))
- `int`, `float`, `complex` and `bool` fields take numpy's integer, floating,
  complex and bool scalars, and read back the Python type. `np.int64`,
  `np.float64`, `np.complex128` and `np.bool_` fields read back as those types
  and hash as `int`, `float`, `complex` and `bool`. ([#66](https://github.com/jacopoabramo/sharedbox/pull/66))
- The extension is compiled for speed instead of size. Writing a `list`, `set`
  or `dict` field, and reading, indexing or looking up a `BoundedList` or
  `BoundedSet`, take less time. The wheels are about 75 KB larger on Linux and
  17 KB larger on Windows. ([#79](https://github.com/jacopoabramo/sharedbox/pull/79))

### Fixed

- A `list`, `set` or `dict` whose slots end short of its alignment, such as
  `Annotated[list[Color], Capacity(3)]` for an `Enum` `Color`, is accepted;
  its size is rounded up to the alignment. `sharedbox` 0.6 refuses a segment
  that holds one. ([#66](https://github.com/jacopoabramo/sharedbox/pull/66))
- A `TypeError` about the sizes of an array member of a dataclass or attrs
  record names its metadata shape, as in `metadata shape (3)`, instead of
  `field(shape=...)`. ([#70](https://github.com/jacopoabramo/sharedbox/pull/70))


## [0.6.0](https://github.com/jacopoabramo/sharedbox/releases/tag/v0.6.0) - 24-10-2026

### Added

- `status::foreign` and `SBX_E_FOREIGN` (-12): the segment's magic is not
  one this version knows. Python raises `SchemaMismatchError`. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))
- `sharedbox::to_expected` and `sharedbox::from_expected`: convert a
  `sharedbox::result` to a `std::expected` and back on C++23. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))
- `KindMismatchError`, a `SchemaMismatchError` raised when a name holds a
  segment of another kind; `status::kind_mismatch` and `SBX_E_KIND` (-11)
  in `sharedbox.hpp` and `sharedbox_c.h`. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))
- `sharedbox::stream`, `sharedbox::stream_sender` and `sharedbox::stream_reader`
  in `stream.hpp`, included by `sharedbox.hpp`: a ring in shared memory with
  one sender and up to `max_readers` readers, each reading
  `read_mode::lossless`, `read_mode::lossy` or `read_mode::latest`, laid out
  as stream layout 1.0. C++ only. `stream_sender::begin`, `publish` and
  `abandon` reserve a slot, fill it in place and publish it or give it up. ([#48](https://github.com/jacopoabramo/sharedbox/pull/48))
- `status::busy` (-13), `status::ended` (-14) and `status::interrupted`
  (-15), with `SBX_E_BUSY`, `SBX_E_ENDED` and `SBX_E_INTERRUPTED` in
  `sharedbox_c.h`. ([#48](https://github.com/jacopoabramo/sharedbox/pull/48))
- `SharedStream`, `SharedSendStream` and `SharedReceiveStream`: send items of
  any type a `SharedBox` field takes from one process to up to `max_readers`
  readers, each reading `lossless`, `lossy` or `latest`, with blocking,
  non-blocking and asyncio calls, `receive_into` into the caller's arrays and
  `receive_future`, a `concurrent.futures.Future`. A stream passed to another
  process arrives attached, or raises `SchemaMismatchError` if its name now
  holds a stream created since, and `SharedStream.create` without a name gives
  the stream a new one. The ends cannot be pickled; `SharedSendStream.stream`
  and `SharedReceiveStream.stream` return their stream. ([#49](https://github.com/jacopoabramo/sharedbox/pull/49))
- `SharedReceiveStream.events`, a `ReceiveEvents` psygnal group whose
  `received` and `ended` signals deliver a reader's items to callbacks. ([#49](https://github.com/jacopoabramo/sharedbox/pull/49))
- `WouldBlock`, `EndOfStream`, `StreamBusyError` and `StreamClosedError`. ([#49](https://github.com/jacopoabramo/sharedbox/pull/49))
- `SharedStreamStatistics` and `ReceiveStreamStatistics`, returned by
  `SharedStream.statistics()`. ([#49](https://github.com/jacopoabramo/sharedbox/pull/49))
- `WaiterSlotsFullError` is exported from `sharedbox`; opening a reader
  raises it when `max_readers` readers are open. ([#49](https://github.com/jacopoabramo/sharedbox/pull/49))
- `benchbox ops`: the row `watched write int`, a write while another process
  has a watcher open. ([#50](https://github.com/jacopoabramo/sharedbox/pull/50))
- `sharedbox::handle::sleepers()`: the number of threads inside a wait on
  the box, in any process. ([#51](https://github.com/jacopoabramo/sharedbox/pull/51))
- `benchbox stream`: items per second of `SharedStream` readers in each mode
  against `mp.Queue` and a `SharedMemory` ring with a lock, and a table of
  `send` and `asend` with `receive`, `async for` and `events.received`,
  with the 50th, 90th and 99th percentile latency. `--short`, `--table`,
  `--json` and `--markdown` are its options; `benchbox all` runs it and
  writes `stream.json`. ([#53](https://github.com/jacopoabramo/sharedbox/pull/53))
- `Streams` workflow: `benchbox stream` on the base branch against the pull
  request, with `scripts/stream_compare.py` writing the table. ([#53](https://github.com/jacopoabramo/sharedbox/pull/53))
- `benchbox roundtrip`: the row `SharedStream`. ([#53](https://github.com/jacopoabramo/sharedbox/pull/53))
- `benchmarks/test_bench_stream.py`: a CodSpeed benchmark of `send` plus
  `receive_into`. ([#53](https://github.com/jacopoabramo/sharedbox/pull/53))
- `FieldWatch.future()`, returning a `FieldFuture`, a
  `concurrent.futures.Future` of the next write to the field; `FieldFuture`
  is exported from `sharedbox`. ([#55](https://github.com/jacopoabramo/sharedbox/pull/55))
- `field(shape=...)`: the sizes of an array field of a box.

  ```python
  class Grid(SharedBox):
      cells: npt.NDArray[np.uint8] = np.zeros((4, 6), np.uint8)
      weights: npt.NDArray[np.float32] = field(shape=(3,))
  ```

  A box array field also takes its sizes from a plain default array, so it
  needs no `Shape` and, with a dtype in its annotation, no `DType`. ([#56](https://github.com/jacopoabramo/sharedbox/pull/56))
- `SharedSendStream.writing()` and `SharedSendStream.awriting()`, which yield a
  `SendSlot`:

  ```python
  blank = Frame(0, np.empty((0, 0), np.uint16))
  with sender.writing(blank) as slot:
      slot.item.image.fill(0)
      slot.send(replace(slot.item, index=1))
  ```

  `slot.item` is an item built from a template, with arrays over the next slot
  to fill in place, and `slot.send(item)` publishes an item without copying the
  arrays already in the slot. ([#58](https://github.com/jacopoabramo/sharedbox/pull/58))

### Changed

- `sharedbox.hpp`: split into `core.hpp` and `box.hpp`, which it includes.
  `sharedbox::result` is the library's own type on C++20 and C++23 alike,
  and its `error()` is a `sharedbox::error` holding the status, the OS error
  and what was found. The C++ names moved from `sharedbox::v2` to
  `sharedbox::v3`. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))
- `sharedbox::header`: the magic, versions, schema hash, creator, waiter
  slot count and size moved into its first member, `common`, a
  `sharedbox::common_header`; `layout_major` and `layout_minor` became
  `common.kind_major` and `common.kind_minor`. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))
- `sharedbox::error`: a `status::os` failure carries the OS error in
  `error().os`; `errno` and `GetLastError()` no longer hold it reliably
  when the call returns. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))
- Segment layout: every segment starts with a common first line (magic,
  core version, layout version of its kind, schema hash, creator), and
  boxes use layout 3.0. A box made by 0.5 cannot be opened by 0.6, nor the
  other way round. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))
- `sharedbox::inspect`: returns `result<common_header>`. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))
- `sharedbox::magic`: renamed `sharedbox::box_magic`. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))
- `box_ref`: a reference field stores the name of its target in 240 bytes,
  so the field takes 256 bytes. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))
- `SharedBox` names: one or more segments of `[A-Za-z0-9_-]` joined by
  `:`, up to 240 characters, such as `bl01:camera:det1:frames`. `.` is no
  longer allowed. The shared memory is `/dev/shm/SBX:<name>` on Linux and
  `Local\SBX:<name>` on Windows, and Windows waiter events end in `#w<i>`. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))
- `SharedBox.attach`: a segment whose magic this version does not know,
  such as a box made by 0.5, raises `SchemaMismatchError` at once, instead
  of `SegmentNotFoundError` after the timeout. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))
- `SharedBox` write: makes a wake call only when a thread is waiting on the
  box. `sleepers`, at offset 84 of box layout 3.0, counts the threads inside
  a wait. It adds 1 to `wake_word` only when `sleepers` is not 0. ([#51](https://github.com/jacopoabramo/sharedbox/pull/51))
- `sharedbox::waiter_slot`: 32 bytes, with `asleep_on`, at offset 24, the
  offset of the count the slot's current wait added 1 to, and a reserved
  word. Freeing the slot of a process killed inside a wait takes that
  thread out of `sleepers` for a box, and out of `data_waiting` or
  `space_waiting` for a stream. ([#51](https://github.com/jacopoabramo/sharedbox/pull/51))
- `benchbox plot`: writes plotly figure JSON (`charts/ops.json`,
  `roundtrip.json`, `stream-throughput.json`, `stream-matrix.json`) instead
  of SVG files. ([#53](https://github.com/jacopoabramo/sharedbox/pull/53))
- `benchmarks` extra and group: depend on `plotly` instead of `matplotlib`. ([#53](https://github.com/jacopoabramo/sharedbox/pull/53))
- Array fields: an annotation that names its number of dimensions, as in
  `numpy.ndarray[tuple[int, int], numpy.dtype[numpy.uint8]]`, raises
  `TypeError` when its `Shape` has another number, in a box, a record and a
  stream item. ([#56](https://github.com/jacopoabramo/sharedbox/pull/56))
- Array fields: a default array whose shape differs from the field's `Shape`
  raises `TypeError` instead of `ValueError` when the class is defined. ([#56](https://github.com/jacopoabramo/sharedbox/pull/56))

### Removed

- Reading box layouts 1.0 and 2.0. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))
- `sharedbox::oldest_layout_major`. ([#47](https://github.com/jacopoabramo/sharedbox/pull/47))


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

[0.5.0]: https://github.com/jacopoabramo/sharedbox/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/jacopoabramo/sharedbox/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/jacopoabramo/sharedbox/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/jacopoabramo/sharedbox/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/jacopoabramo/sharedbox/compare/0.2.4...v0.3.0
[0.2.4]: https://github.com/jacopoabramo/sharedbox/compare/0.1.0...0.2.4
[0.1.0]: https://github.com/jacopoabramo/sharedbox/commits/0.1.0
