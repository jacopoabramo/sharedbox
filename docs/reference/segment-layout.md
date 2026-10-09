---
icon: lucide/file-text
---

# Segment layout

This page is the full contract for how a box and a stream are stored in
shared memory: the names of the objects, every byte of the mapping, and the
steps every process follows to read, write, send, receive and wait. You
need it if you write code that opens a box without `sharedbox.hpp`, or if
you change `sharedbox` itself;
to understand the ideas first, read
[How a box is stored](../explanation/how-a-box-is-stored.md).
`include/sharedbox/core.hpp`, `include/sharedbox/box.hpp` and
`include/sharedbox/stream.hpp` implement this contract, and the Python
extension runs on the first two.

It describes core version 1.0, box layout 3.0 and stream layout 1.0, which
`sharedbox` 0.6.0 writes. It opens nothing older: box layouts 1.0 and 2.0,
which 0.5 and earlier wrote, are refused.

## Goal

C++ and C code can use a sharedbox box, both as an extension loaded in a
Python process and as a standalone program with no Python, without linking
a library that sharedbox ships. The layout and protocols below are
versioned, and a header-only C++20 library implements them.

## Decisions

| Topic | Decision | Not taken |
| --- | --- | --- |
| Model | the Arrow PyCapsule Interface; the closest analogue is `ArrowArrayStream`, a live handle rather than a one-shot transfer | numpy's `__array_struct__` |
| Scope | every segment starts with the same first line; the box and the stream are the kinds implemented, and the stream is C++ only; the handle struct is versioned so that streams can get their own dunder later | streams in the same step |
| Contract | a documented memory layout plus a header-only reference implementation | a compiled C library; definitions only |
| Language | the C++20 headers `core.hpp` and `box.hpp`, which `sharedbox.hpp` includes, namespace `sharedbox` | a C99 header with `static inline` functions |
| Atomics | `std::atomic_ref` on plain integer fields | wrappers over compiler intrinsics; C11 `<stdatomic.h>` |
| C interface | a minimal `sharedbox_c.h`, whose functions call `sharedbox.hpp`, in one `.cpp` file the consumer compiles | a static library in the wheel; a C implementation of the protocols |
| Dunder and capsule | `__sharedbox_box__`, capsule `"sharedbox_box"` | `_c_` in the name, which reads as "implemented in C" |
| Negotiation | DLPack's `max_version`; the consumer renames the capsule `"used_sharedbox_box"`; other keywords raise `NotImplementedError`, as in Arrow | none |
| Object names | `SBX:` + box name + suffix, for every object | the bare box name; names stored in the header |
| Windows scope | `Local\` only | `Global\` (see Out of scope) |
| Versioning | the core version and the box layout each have a major that refuses and a minor that opens and ignores what it does not know | exact match; flag masks |
| Capsule lifetime | the handle maps the segment again from the box's OS handle | sharing the box's mapping and keeping the box alive |
| Schema identity | `identity=` class keyword, default `module.qualname`; it enters the schema hash and names the box; `handle::create` is available to other languages | the Python module path written into other languages; open-only for other languages |
| Distribution | headers in the wheel, `sharedbox.get_include()` and a CMake config | a vcpkg port or a headers-only wheel |

## Names

Every OS object of a box is named from the box name:

| Object | Linux | Windows |
| --- | --- | --- |
| mapping | `shm_open("/SBX:<name>")`, seen as `/dev/shm/SBX:<name>` | `CreateFileMappingW(INVALID_HANDLE_VALUE, ..., L"Local\\SBX:<name>")` |
| wake word | inside the mapping, no name | not used |
| waiter slot `i` | inside the mapping, no name | auto-reset event `Local\SBX:<name>#w<i>` |

- A name is one or more segments of `[A-Za-z0-9_-]` joined by `:`, such as
  `bl01:camera:det1:frames`, up to 240 characters. A segment is never
  empty, so a name neither starts nor ends with `:` and has no `::`. `.` is
  reserved for a later `name.field` address, and `#` for the waiter event
  suffix. Every function that takes a name returns `status::range` for one
  that breaks these rules. The longest Windows object name, `Local\SBX:`
  (10 characters), a 240-character name and `#w4095` (6), is 256
  characters, under the 260-character limit on object names. On Linux the
  file name `SBX:` plus the name is 244 bytes, under the 255 of
  `NAME_MAX`.
- The default name of a class is 16 hex digits of SHA-256 over its identity
  (see Schema identity). Without an `identity=` keyword the identity is
  `module.qualname`, with `__mp_main__` counted as `__main__`.
- Linux: the mapping is created with `O_CREAT | O_EXCL | O_RDWR` and mode
  `0600`; a name that exists gives `status::exists`. `unlink()` calls
  `shm_unlink`.
- Windows: the mapping is backed by the page file. `CreateFileMappingW`
  reporting `ERROR_ALREADY_EXISTS` means the name is taken: the creator
  closes its handle and gives `status::exists`. `ERROR_INVALID_HANDLE`, an
  object of another kind under the name, gives `status::exists` too.
  `unlink()` does nothing; the OS frees the mapping with its last handle.

## Layout

The first 64 bytes of every segment, whatever its kind, are the common
line: the same fields at the same offsets, so a process can tell what it has
found before it knows how to read it. A box then adds a second line and the
rest of its mapping. The mapping holds, from offset 0:

```text
offset 0     common line       64 bytes, one cache line (every kind)
offset 64    box line          64 bytes, one cache line
offset 128   field table       field_count x 8 bytes
             write counts      field_count x 8 bytes
             waiter slots      waiter_slots x 32 bytes
             description table types_size bytes, 8-aligned
             (zero padding up to a multiple of 64)
record       record            record_size bytes, 64-byte aligned
             (zero padding up to a multiple of 4096)
```

No structure has implicit padding: gaps are explicit `pad` and `reserved`
members, and `static_assert`s in `core.hpp` and `box.hpp` pin each
structure's `sizeof`, `alignof` and every `offsetof`, and that it is
standard-layout and trivially copyable.

```cpp
struct common_header {              // sharedbox::common_header, 64 bytes
    uint64_t magic;                 //  0  one per kind; written last, with release ordering
    uint16_t core_major;            //  8
    uint16_t core_minor;            // 10
    uint16_t kind_major;            // 12  the layout version of this kind
    uint16_t kind_minor;            // 14
    uint64_t schema_hash;           // 16
    uint64_t create_id;             // 24  random at create, never 0
    uint64_t creator_start;         // 32  creator's start time, see Liveness
    uint64_t creator_pidns;         // 40  creator's pid namespace; 0 on Windows
    uint32_t creator_pid;           // 48
    uint16_t waiter_slots;          // 52  1 to 4096
    uint16_t reserved0;             // 54  zero
    uint64_t size;                  // 56  mapping size in bytes
};

struct header {                     // sharedbox::header, 128 bytes
    common_header common;           //  0
    // line 1: the words writes and waits change, then geometry written once
    uint64_t seq;                   // 64  sequence lock: even when free, odd while writing
    uint32_t writer_pid;            // 72  holder of the write lock, 0 if none
    uint32_t wake_word;             // 76  futex word (Linux)
    uint32_t waiters;               // 80  occupied waiter slots
    uint32_t sleepers;              // 84  threads inside a wait on this box
    uint16_t field_count;           // 88  1 to 256
    uint16_t reserved1;             // 90  zero
    uint32_t record_size;           // 92
    uint32_t record;                // 96  offset of the record
    uint32_t tail;                  // 100 offset of the field table, always 128
    uint32_t types_size;            // 104 bytes of the description table
    uint8_t  reserved2[20];         // 108 zero; minor versions may use it
};
```

The common line is written once at creation. Line 1 starts with the words
that writes and waits change (`seq`, `writer_pid`, `wake_word`, `waiters`,
`sleepers`), so a write touches one header line, and ends with geometry
written once at creation, bytes 88 to 127. Attach copies the common line and
those bytes, never the five changing words, and uses only the copy.

The box line has spare bytes so that minor versions can add fields: a
header with no room left would move the fields after it with every
addition. There is one header per box, and readers and writers touch only
line 1. The waiter slots, not the header, are the large part of the tail, and
`max_waiters` sizes them. Worked example, a box with an `int`, a `float`
and a `bool` and the default 64 waiter slots: header 128, field table 24,
write counts 24, waiter slots 2048, padding 16, record 24 (the Python side
rounds a record up to a multiple of 8): 2264 bytes used, one 4 KiB page. A
box with three fields and 64 slots stays in one page up to about 1.8 KB of
record (4096 - 2240 = 1856).

- Versions: bytes 8 to 11 are `01 00 00 00` (core 1.0: `core_major` 1,
  `core_minor` 0) and bytes 12 to 15 are `03 00 00 00` (box layout 3.0:
  `kind_major` 3, `kind_minor` 0). The two versions count the segment
  format and are not tied to the package version.
- Magic: `SBX_BOX_` for a box and `SBX_STRM` for a stream, each read as a
  little-endian `u64`. The magic names the kind, so attach checks it first.
  A box opens only a box, and a stream only a stream (see Stream layout).
- Field table entry, 8 bytes: `u32 offset`, `u32 capacity_and_kind`: top 8
  bits the kind code. For kinds below 64 the low 24 bits are the size or
  capacity; for kinds 64 and up they are the offset of the field's
  description in the table.
- Offsets are the creator's choice. The Python side packs fields by
  descending alignment; another creator may pack differently. Attachers
  read offsets from the field table and never compute them, so a box
  created elsewhere with its own packing reads correctly from Python as
  long as the field order, names, kinds and capacities match, which the
  schema hash checks.
- Write counts: one `u64` per field, incremented under the write lock.
- Waiter slot, 32 bytes:

  ```cpp
  struct waiter_slot {              // sharedbox::waiter_slot
      uint64_t owner_start;         // 0   process start time, see Liveness
      uint64_t owner_pidns;         // 8   pid namespace, see Liveness; 0 on Windows
      uint32_t owner_pid;           // 16  0 = free
      uint32_t interrupt;           // 20  set by interrupt(), cleared by the waiter
      uint32_t asleep_on;           // 24  offset of the count the current wait added 1 to; 0 = none
      uint32_t reserved;            // 28  0
  };
  ```

- A `ref` field refers to another box, which keeps its own segment. Its
  capacity is always 256:

  ```cpp
  struct box_ref {                  // sharedbox::box_ref
      uint64_t create_id;           // 0   the box's create_id; 0 = empty
      uint64_t schema_hash;         // 8   the box's schema_hash
      char     name[240];           // 16  its name, NUL-padded; ends at the first NUL or at 240
  };
  ```

  An empty reference is 256 zero bytes. A non-empty one always has a
  nonzero `create_id`, since create never draws 0. It is written under the
  referring box's sequence lock like any other field. A reader that
  attaches the named box compares its `create_id` with the stored one to
  tell it from a box created again under the same name.

### Kind codes

| code | kind | entry's low 24 bits | size, alignment |
| --- | --- | --- | --- |
| 0 | bool | 1 | 1, 1 |
| 1 | int | 8 | 8, 8 |
| 2 | float | 8 | 8, 8 |
| 3 | str | capacity | 4 + capacity, 4 |
| 4 | bytes | capacity | 4 + capacity, 4 |
| 5 | ref | 256 | 256, 8 |
| 6 | complex | 16 | 16, 8 |
| 7 | date | 4 | 4, 4 |
| 8 | time | 16 | 16, 8 |
| 9 | datetime | 16 | 16, 8 |
| 10 | timedelta | 12 | 12, 4 |
| 11 | uuid | 16 | 16, 1 |
| 12 | decimal | capacity | 4 + capacity, 4 |
| 64 | enum | description offset | 2, 2 |
| 65 | flag | description offset | 8, 8 |
| 66 | literal | description offset | 2, 2 |
| 67 | optional | description offset | from the description |
| 68 | union | description offset | from the description |
| 69 | record | description offset | from the description |
| 70 | tuple | description offset | from the description |
| 71 | list | description offset | from the description |
| 72 | set | description offset | from the description |
| 73 | dict | description offset | from the description |
| 74 | array | description offset | data size, 64 |

A field of a kind the reader does not know is opened as opaque bytes: a
kind below 64 is sized by its entry, one from 64 by its description's
head. Every described size is a multiple of its alignment.

### Encodings

Little-endian.

- `bool`: 1 byte, `0x00` or `0x01`.
- `int`: 8 bytes, signed.
- `float`: 8 bytes, IEEE 754 double.
- `str`, `bytes`: `u32` length, then up to `capacity` bytes (UTF-8 for
  `str`).
- `ref`: 256 bytes, as `box_ref` above.
- `complex`: two `f64`, real then imaginary.
- `date`: `i32` `date.toordinal()`, 1 to 3652059.
- `time`: `i64` microseconds since midnight (wall time), `i16` UTC offset
  in minutes, `u8` flags (bit 0 naive, bit 1 fold), 5 reserved zero bytes.
- `datetime`: `i64` microseconds since 1970-01-01T00:00 (wall time if
  naive, UTC if aware), `i16` offset in minutes, `u8` flags as `time`, 5
  reserved zero bytes.
- `timedelta`: `i32` days, `i32` seconds, `i32` microseconds.
- `uuid`: `UUID.bytes`, in network byte order.
- `decimal`: `u32` length, then the text of `str(value)`.
- `enum`, `literal`: `u16` position; `flag`: `u64` bits.
- `optional`: a presence byte, padding to the member's alignment `A`, then
  the member; size `round_up(A + member size, A)`.
- `union`: a `u8` tag, padding to the largest member alignment `A`, then
  the member; size `round_up(A + largest member size, A)`.
- `record`, `tuple`: members at the offsets their entries give.
- `list`, `set`: `u32` length, padding to `max(4, A)`, then `capacity`
  slots of `round_up(element size, element alignment)` bytes.
- `dict`: as `list`, with a key and value pair as the slot: the key at 0,
  the value at `round_up(key size, value alignment)`.
- `array`: the elements in C order.

### Descriptions

The description table follows the waiter slots. Each description is an
8-byte head, `u8 kind`, `u8 flags` (zero), `u16 count`, `u32 size`, a
body, and zero padding to a multiple of 8. An entry inside a body has the
shape of a field table entry, with the offset counted from the start of
the value. A name is a `u16` length, then UTF-8.

| kind | count | body |
| --- | --- | --- |
| enum | 1 to 65535 | `count` member names |
| flag | 1 to 64 | `count` members: a name, then `u64` bits |
| literal | 1 to 65535 | `count` values: a `u8` tag, then 0 None (nothing), 1 bool (`u8`), 2 int (`i64`), 3 str (a name), 4 bytes (`u32` length, then bytes), 5 enum member (a name) |
| optional | 1 | one entry |
| union | 2 to 255 | `count` entries; member `i` has tag `i` |
| record | 1 to 256 | `count` entries, then `count` member names |
| tuple | 1 to 256 | `count` entries |
| list, set | 1 | `u32` capacity, then the element's entry |
| dict | 2 | `u32` capacity, then the key's entry and the value's |
| array | 0 | `u8` DLPack code, `u8` bits, `u16` lanes (1), `u8` ndim (1 to 8), 3 zero bytes, then `ndim` `u64` dimensions |

Attach copies the table and checks it before using it: every description
is 8-aligned, inside the table, read once, after its parent and apart from
every other; nesting is at most 16 deep; counts are in the ranges above;
members lie inside their parent, aligned and apart; every size the
description implies is computed without overflow and matches the head; an
array's code is one of 0, 1, 2, 4, 5, 6, its bits a multiple of 8 and every
dimension at least 1. A failure refuses the segment. A reference may only
be a field, and an array only a field or a record member.

### Decoding checks

A stable sequence number shows a copy is consistent, not that it holds a
value Python can have. Readers check presence bytes and `bool` (0 or 1),
reserved flag bits (0), union tags and enum and literal positions (below
their count), lengths (at most the capacity), date ordinals (1 to
3652059), times (below a day), offsets (within 1439 minutes either way)
and timedeltas (within Python's range), and treat a failure as a corrupt
value.

Only a `bool` inside a described kind has to be 0 or 1. A `bool` field
(kind 0) whose byte is not 0 reads as `True`.

### Writing a list, set or dict

The bytes written for a list, set or dict field are its length and the
slots it uses, shorter than the field when it is not full; a reader copies
the same part.

## Schema identity

The schema is the list of fields that fixes a record's shape: names, kinds,
capacities and their order. The schema hash shows that a creator and an
attacher agree on it and on the class's meaning.

- Hash text: the identity, then one `name:kind:capacity` per field in
  declaration order (base class fields first), joined with `|`, encoded as
  UTF-8. Kinds are the words `bool`, `int`, `float`, `str`, `bytes`;
  capacity is the payload size in bytes (8 for `int` and `float`, 1 for
  `bool`). A `ref` field enters as `name:ref:<identity>`, or
  `name:ref?:<identity>` when it may be empty (annotated `X | None`), with
  the identity of the class it refers to in place of the capacity.
- A field of a kind from 6 on enters as `name:<type text>`. The type text
  of each kind, with `T` for the type text of a member:
    - `complex`, `date`, `time`, `datetime`, `timedelta`, `uuid`: the kind
      name alone. `bool:1`, `int:8` and `float:8` are the texts of those
      kinds inside a described type.
    - `str:n`, `bytes:n` and `decimal:n`: `n` is the capacity in bytes.
      A `bytearray` is `bytes:n`.
    - `enum(A,B)`: the member names in definition order.
      `flag(R=1,W=2)`: each member name with its value.
    - `literal(v,...)`: each value in order as `None`, `True` or `False`,
      `int:1`, `str:'x'` (the Python `repr`), `bytes:b'x'` or
      `member:NAME` for an enum member.
    - `optional(T)`, and `union(T,...)` with the members in the order
      stored.
    - `record(name:T,...)` for a dataclass, `NamedTuple`, `TypedDict`,
      attrs class or `msgspec.Struct`, with the members in order. A
      `TypedDict` key that may be missing enters as `name?:optional(T)`.
    - `tuple(T,...)` for a fixed-length tuple.
    - `list[n](T)`, `set[n](T)` and `dict[n](K,V)`: `n` is the capacity in
      elements. A `frozenset` is a `set`; `tuple[T, ...]` is a `list`.
    - `array(dtype,d1xd2x...)`, as in `array(float32,2x3)`.

- `schema_hash`: the first 8 bytes of SHA-256 over that text, read as a
  little-endian `u64`.
- Identity: the `identity=` class keyword, a non-empty string; without it,
  `module.qualname`. A subclass does not inherit its base's identity.
- The default box name is 16 hex digits of SHA-256 over the identity, so a
  class that sets `identity=` is found by that identity from any package or
  language, with no explicit name.
- A set identity lets another language agree with Python on a string both
  sides write down, rather than on a Python module path. Moving or renaming
  the Python class then keeps the hash, two packages can each declare a
  matching class and share a box, and changing the identity (`"motor/2"`)
  refuses processes of the old meaning when no field changed (a change of
  units, for instance).
- `sharedbox.hpp` does not carry SHA-256; C and C++ callers compute the
  hash with their own library.
- Test vector:

  ```text
  __main__.Motor|position:int:8|enabled:bool:1|label:str:32
  schema_hash  = 0x82ce467598596a72
  default name = 5b4f7004d44277b7    (16 hex digits of SHA-256 over "__main__.Motor")

  __main__.Stage|label:str:4|motor:ref:motor/1
  schema_hash  = 0xe33ffd91e10fab6d

  __main__.Stage|label:str:4|motor:ref?:motor/1
  schema_hash  = 0xe58153f79aa6fdf6

  demo/1|when:date|mode:enum(RED,BLUE)|tags:list[2](str:4)|note:optional(int:8)
  schema_hash  = 0x2a9e5c5cc2876a4c
  ```

  The last line is a class with `identity="demo/1"` and the fields
  `when: date`, `mode: Color` (members `RED` and `BLUE`),
  `tags: Annotated[list[Annotated[str, Capacity(4)]], Capacity(2)]` and
  `note: int | None`.

## Stream layout

A stream is a ring of `capacity` slots, at least 2, that one sender writes
and up to `max_readers` readers (1 to 4095) copy out of. Its common line
holds the magic `SBX_STRM` (`0x4D5254535F584253`), `kind_major` 1 and
`kind_minor` 0 (stream layout 1.0), and a `waiter_slots` of
`max_readers + 1`, one slot for each reader and one for the sender. Every
size and offset is 64-bit, so a ring may pass 4 GiB; the slots end at or
below 2^46 bytes. The mapping holds, from offset 0:

```text
offset 0     common line       64 bytes (every kind)
offset 64    line 1            written once at creation
offset 128   line 2            the sender's words
offset 192   line 3            the readers' words
offset 256   reader table      max_readers x 64 bytes
             waiter slots      (max_readers + 1) x 32 bytes
             (zero padding up to a multiple of 64)
slots        slots             capacity x slot_size bytes
types        description table types_size bytes
             (zero padding up to a multiple of 4096)
```

```cpp
struct stream_header {              // sharedbox::stream_header, 256 bytes
    common_header common;           //   0
    // line 1: written once at creation
    uint64_t capacity;              //  64  slots in the ring, at least 2
    uint64_t slot_size;             //  72  bytes per slot, a multiple of 64
    uint64_t readers;               //  80  offset of the reader table, always 256
    uint64_t slots;                 //  88  offset of the first slot
    uint64_t types;                 //  96  offset of the description table
    uint32_t types_size;            // 104  bytes of the description table
    uint32_t item_entry;            // 108  the item's capacity_and_kind
    uint32_t max_readers;           // 112  1 to 4095
    uint32_t reserved1;             // 116  zero
    uint64_t reserved2;             // 120  zero
    // line 2: the sender's words
    uint64_t write_pos;             // 128  items sent; the next send's position
    uint64_t sender_start;          // 136  start time of the sender's process
    uint64_t sender_pidns;          // 144  its pid namespace; 0 on Windows
    uint32_t sender_pid;            // 152  0 = no sender
    uint32_t state;                 // 156  0 open, 1 ended
    uint32_t data_word;             // 160  readers sleep on it (Linux)
    uint32_t readers_epoch;         // 164  changed when a reader joins, leaves or is freed
    uint32_t space_waiting;         // 168  sender threads asleep on space_word
    uint8_t  reserved3[20];         // 172  zero
    // line 3: the readers' words
    uint32_t space_word;            // 192  the sender sleeps on it (Linux)
    uint32_t lossless_readers;      // 196  open lossless readers
    uint32_t data_waiting;          // 200  reader threads asleep on data_word
    uint32_t waiters;               // 204  claimed waiter slots
    uint8_t  reserved4[48];         // 208  zero
};

struct reader_entry {               // sharedbox::reader_entry, 64 bytes
    uint64_t position;              //  0  the next position this reader reads
    uint64_t owner_start;           //  8  process start time, see Liveness
    uint64_t owner_pidns;           // 16  pid namespace; 0 on Windows
    uint32_t owner_pid;             // 24  0 = free
    uint32_t mode;                  // 28  0 until the entry has a position, 1 lossless, 2 lossy, 3 latest
    uint8_t  reserved[32];          // 32  zero
};
```

- `waiters` and the waiter slots are the core's; see Waiter slots. A waiter
  slot's index is not tied to a reader entry's index.
- Slot `i` starts at `slots + i * slot_size`. It holds an 8-byte `seq` at
  its start and the item at `max(8, item alignment)`, so an item aligned to
  64 bytes (an array, or a record holding one) starts 64 bytes in.
  `slot_size` is the end of the item rounded up to a multiple of 64.
- The item is the bytes of one value in the encodings above, a length prefix
  included, described by `item_entry` (a `capacity_and_kind` word, as a
  field table entry has) and the description table. An item of a kind
  this build does not know is refused.
- The item's `seq` for position `p` is `2p + 1` while the sender writes it
  and `2p + 2` once it is published. 0 means the slot was never written.
  Position `p` lives in slot `p % capacity`.
- Open copies lines 0 and 1, checks only that copy and uses it; lines 2
  and 3 change while the stream is open. It checks the common line as for
  every kind, with `kind_major` 1. It then requires every offset to be the one the
  shape gives: the reader table at 256, the waiter slots right after it, the
  slots at the next multiple of 64, the description table right after the
  slots, and a mapping size of the description table's end rounded up to
  4 KiB. `capacity` must be at least 2, `max_readers` 1 to 4095,
  `waiter_slots` `max_readers + 1`, `slot_size` a multiple of 64, and
  `types_size` a multiple of 8 and at most 16 MiB. A failed check is
  `status::corrupt`. Last, open parses the item type and requires that
  `slot_size` is what the item needs.

## Versioning rules

A segment carries two versions in its common line: the core version, for
what every kind shares (the common line, the object names, liveness), and the
layout version of its kind, for the rest. Each has a major and a minor.

- A reader refuses a `core_major` it does not know: this version opens core
  major 1. It also refuses a `kind_major` its kind does not know: this
  version opens box layout major 3 and stream layout major 1, so it does not
  open segments written by 0.5 and earlier, whose magic it does not know.
- A reader opens a segment whose `core_minor` or `kind_minor` is higher than
  its own, and ignores what it does not know. A handle reports the lower of
  the segment's box minor and its own.
- A minor version may only add: fields in bytes that are reserved and zero
  in older minors, or features that stay off unless the segment says they
  are on and the reader knows them.
- That includes field kinds: `handle::open` and `handle::from_capsule`
  accept a field whose kind code they do not know. Its bytes are otherwise
  opaque, and the field must lie inside the record without overlapping
  another. A reader opens and reads a field of a kind it does not know; it
  never writes one, and `write` returns `status::range` for it. The Python
  extension, which converts every field, refuses such a segment with
  `SchemaMismatchError`.
- For an unknown kind below 64, the entry's low 24 bits are the field's
  whole span in the record (1 byte to 1 MiB), so a future kind with a length
  prefix counts the prefix in it, and no alignment is asked. For an unknown
  kind from 64 on, the low 24 bits are the offset of its description, whose
  head's `size` is the span, and no alignment is asked either.
- Box layout 3.0 has the description table and the kinds 6 to 12 and 64 to
  74. A later 3.x minor version may add kinds; a reader opens a field of a
  kind it does not know as opaque bytes.
- A change to how existing bytes are read or written raises a major: the
  core's (the common line, the object names, liveness) raises `core_major`,
  and a kind's own (the sequence lock, field encoding, the slot layout)
  raises its `kind_major`.
- The package takes a semver major step whenever a major changes; before
  1.0, a minor version step does.

## Protocols

Every shared word is a plain integer in the mapping, accessed through
`std::atomic_ref` with the orderings given here.

### Create

1. Create the mapping (see Names) at its full size, rounded up to 4096.
   On Linux the size is reserved with `posix_fallocate`, retried on
   `EINTR`, so a `/dev/shm` too small for the box fails here with an OS
   error instead of a later `SIGBUS`; `ftruncate` is the fallback where
   the file system does not support it. New pages read as zero on both
   platforms.
2. Write the field table, the creator fields and the rest of the common
   line except `magic`, then the box line's geometry. `core_major`,
   `core_minor`, `kind_major` and `kind_minor` are the versions of this
   library.
   `create_id` comes from the OS random source (`getrandom` on Linux,
   falling back to `/dev/urandom` where it is missing or refused;
   `BCryptGenRandom` on Windows) and is drawn again if 0. The creator
   fields are this process's pid, start time and pid namespace. Every
   waiter slot is free because the pages are zero.
3. Write the initial field values into the record.
4. Store `magic` (`SBX_BOX_`) with release ordering. Until then no attach
   succeeds.
   `handle::create` does this at once. `handle::create_unpublished`
   stops before it, so the creator can write through its handle first
   (the Python layer runs `__post_init__` there), and
   `handle::publish()` stores `magic` later, with a compare-exchange
   from 0; it returns `status::range` when `magic` is already stored,
   which is always the case for a handle made by `open` or
   `from_capsule`.
5. On a failure after step 1, remove the name (Linux) and close.

### Attach

1. Open the mapping by name and map all of it. Linux: `fstat` gives the
   size, and a mapping smaller than one page is waited for, since its
   creator sizes it right after making the name. Windows: `VirtualQuery`
   on the view gives the size.
2. Wait for `magic` with acquire ordering, with backoff (spin, yield, then
   sleeps doubling up to 1 ms), up to the timeout. A mapping that never
   gets `magic` is `status::not_found`.
3. Copy the common line and bytes 88 to 127 of the box line, leaving out
   the five words that writes and waits change, and check only the copy.
   Checks run in this order, and the first to fail decides the status:
    1. The magic is one this library knows, else `status::foreign`: the
       segment was made by another version of `sharedbox` or is not a
       `sharedbox` segment.
    2. The magic is the one of a box, else `status::kind_mismatch`: the
       name holds a segment of another kind.
    3. `core_major` is 1, else `status::layout`.
    4. `kind_major` is 3, else `status::layout`.
    5. `size` equals the mapping size and `waiter_slots` is in 1 to 4096,
       else `status::corrupt`.
    6. Every geometry field against the mapping size, before reading
       through it: the mapping is at most 2^32 - 4096 bytes, `field_count` is in range,
       `tail == 128`, the record is 64-byte aligned, after the waiter
       slots and inside the mapping, with `record + record_size` at most
       2^32 - 4096, then each field table entry (capacity 1 byte to
       1 MiB, and the sizes of Kind codes for the fixed kinds, alignment
       for the known kinds, the field inside the record, no two fields
       overlapping), then the description table (Descriptions). A field
       of an unknown kind is opaque (Versioning rules). A failed check is
       `status::corrupt`.
   For `foreign` and `kind_mismatch`, the error's `found` holds the magic
   that was read. For `layout` it holds both versions:
   `core_major << 48 | core_minor << 32 | kind_major << 16 | kind_minor`.
4. Copy the field table and use only the copy afterwards.
5. Free the waiter slots of dead processes (see Waiter slots).

`handle::open` does not compare schema hashes: the caller compares
`schema_hash()` with its own.

### Sequence lock

- Read: load `seq` with acquire; if odd, back off and retry; copy the
  field and load its write count; an acquire fence; load `seq` again and
  retry if it moved. The count is read inside the same window, so a value
  and its version always match.
- Write: compare-and-swap `seq` from even `s` to `s + 1` (acquire), then a
  release fence; store `writer_pid`; write the values; increment each
  written field's count; unlock; wake.
- Unlock: if `seq` still equals `s + 1`, clear `writer_pid`; then
  compare-and-swap `seq` from `s + 1` to `s + 2` (release), and do nothing
  if the swap fails. A failed swap means a `force_unlock()` released this
  writer's lock while it was still running, and `seq` may now belong to a
  later writer's lock. A plain store of `s + 2` would release that later
  writer's lock and move `seq` backwards. The check and the clear are two
  operations, so a `force_unlock()` and a new writer's lock that both land
  between them leave the new writer's `writer_pid` at 0; the lock is not
  affected, and only a later `LockTimeoutError` names pid 0 instead of
  the holder.
- Generation: there is no separate counter. Every write adds exactly 2 to
  `seq`, so the generation is `seq >> 1` (`generation()` returns it). A
  `force_unlock()` that stores `seq + 1` counts as one generation, since
  the dead writer may have changed data.
- A reader or writer gives up after the lock timeout with
  `status::lock_timeout`.
- `force_unlock()`: if `seq` is odd, compare-and-swap it to `seq + 1`;
  `writer_pid` stays as it was, as a record of who held the lock.

### Waiter slots

- `waiter_slots` is set at creation: the `max_waiters` class keyword,
  default 64, 1 to 4096.
- Register: first free every slot whose owner is dead (Liveness). Claim a
  slot with a compare-and-swap of `owner_pid` from 0 to this process's
  pid, then increment `waiters`, clear `interrupt`, store `owner_pidns` and
  last `owner_start` (release). A slot with a nonzero `owner_start` has
  always been counted, so freeing it may decrement `waiters`. A slot whose
  `owner_start` is still 0 belongs to a claim in progress; if its pid is no
  longer running, the claimer died mid-claim and the slot is freed without
  a decrement. A claimer killed between its compare-and-swap and its
  increment therefore cannot push `waiters` below the true count; one
  killed after the increment leaves it one too high, which costs a system
  call on each wake and never loses one. No slot free gives
  `status::no_slot`.
- Free a dead owner's slot: change `owner_start` with a compare-and-swap
  from the value read to `UINT64_MAX - 1` (the `start_freeing` marker),
  which is never a start time on Linux (clock ticks since boot) or Windows
  (FILETIME). Only the process whose compare-and-swap succeeds goes on,
  and every scan skips a slot holding the marker. It then checks that
  `owner_pid` still holds the pid it read; if not, it puts the old
  `owner_start` back and leaves the slot to its new owner. Otherwise it
  decrements `waiters` if the value it read was nonzero, then exchanges
  `asleep_on` for 0. If the old value is the offset of a count this kind
  keeps (a box's `sleepers` at 84; a stream's `space_waiting` at 168 or
  `data_waiting` at 200), the owner died inside a wait and the freer
  subtracts 1 from that count; any other value is ignored, so a corrupt
  slot cannot make the freer write elsewhere. It then stores
  `owner_pidns = 0`, then `owner_pid = 0` (release), and last changes
  `owner_start` from the marker to 0 with a compare-and-swap, which fails
  harmlessly once a new claimer has stored its own.
- A scan reads `owner_pid`, then `owner_start` and `owner_pidns`, then
  `owner_pid` again, and skips the slot if the pid changed: otherwise a
  slot freed and claimed between the first two reads would pair the dead
  pid with the new owner's start, and the compare-and-swap would mark a
  live slot.
- Release: store `owner_start = 0` (release), decrement `waiters`, store
  `owner_pidns = 0`, then `owner_pid = 0` (release). A handle frees only a
  slot it claimed that still records its own `(pid, start, pidns)`; a
  child created by `fork` inherits its parent's claims but leaves them to
  the parent.
- A thread waits in a slot it holds, one thread at a time, and a slot is
  not released while a wait in it runs. The Python watcher registers one slot
  for its lifetime, and a one-off `Segment.wait()` without a slot claims
  one for the call.
- The slot table and the protocol above are the core's, shared by every
  kind. A box's `waiters` is the count of claimed slots in its header; a
  stream's is `waiters` in its line 3, and the stream sizes its table at
  `max_readers + 1`.

Known limits, each after a process is killed at one specific step:

- A freer killed while holding the marker, before it clears `owner_pid`,
  leaves the slot unusable until the segment is created again, and
  `waiters` one too high if it was killed before its decrement. Killed
  after it won the slot and before its subtraction of the sleeper count, it
  leaves that count one too high and the slot stuck with the marker.
- An owner killed between its exit exchange of `asleep_on` and its
  subtraction of the sleeper count leaves that count one too high.
- An owner killed partway through a release leaves a slot with no start,
  which is freed without a decrement, so `waiters` stays one too high if
  the kill came before the decrement. On Linux, a kill after
  `owner_pidns = 0` leaves a namespace of 0, which is never freed (see
  Liveness); the count stays correct.
- A claimer killed on Linux before it stores `owner_pidns` leaves the slot
  stuck the same way, and counted once too many if it had already
  incremented `waiters`.
- The check that `owner_pid` still holds the pid cannot tell an unstamped
  claimer from another one with the same pid, which needs the pid to be
  reused within a few instructions.
- A thread killed inside a wait after it adds 1 to the sleeper count and
  before it stores the count's offset in `asleep_on` leaves that count one
  too high, for a box and for a stream alike. Killed later in the wait,
  before its exit exchange of `asleep_on`, it leaves `asleep_on` set, and
  freeing its slot takes the 1 back.

A count that is too high costs a system call on each write; it never loses
a wake-up.

### Wait and wake

- Wait (slot `i`, last seen generation `g`, timeout):
  - Add 1 to `sleepers`, then store its offset, 84, in slot `i`'s
    `asleep_on`, both sequentially consistent, for the whole wait. Then
    load `wake_word` (sequentially consistent), then return at once if
    `seq >> 1 != g` or slot `i`'s `interrupt` is set (clearing it with a
    compare-and-swap).
  - On every return, exchange `asleep_on` for 0 and subtract 1 from
    `sleepers` only if the exchange gave a nonzero value. The order keeps
    the count from going below the number of threads inside a wait: a
    freer of the slot takes the 1 back only after the store, and only once.
  - Linux: `FUTEX_WAIT` (shared, not private) on `wake_word` with the value
    loaded before the check, so a write in between makes the call return
    at once.
  - Windows: wait on event `i`, which the waiter's process opens on first
    use and keeps.
  - Check again after waking; a wake may be spurious. Past the timeout the
    result is `status::timeout`.
- Wake, after every write:
  - The swap that unlocks `seq` is sequentially consistent. Then load
    `sleepers` (sequentially consistent). If it is 0, stop: no change to
    `wake_word` and no system call on the normal path. The load comes before
    the waiter's increment of `sleepers` in the single order of sequentially
    consistent operations, and the swap comes before the load, so the
    waiter's check of `seq` sees the new value.
  - Otherwise increment `wake_word` (sequentially consistent) before the
    wake. A Linux waiter whose load of `wake_word` came before the increment
    is refused sleep or woken. One whose load comes after it reads this
    increment or a later change, every change to `wake_word` being a
    read-modify-write, so the swap happens before its check of `seq`. On
    Windows the value of `wake_word` is not used, and the waiter's load stays.
  - Linux: one `FUTEX_WAKE` with `INT_MAX` waiters.
  - Windows: `SetEvent` on the event of every occupied slot.
- `interrupt(slot)`: set the slot's `interrupt`, then wake that slot. On
  Linux it increments `wake_word` and calls `FUTEX_WAKE`, and the other
  waiters check their own flag and sleep again; on Windows it sets that
  slot's event only. The flag stays set until the waiter sees it, so an
  interrupt sent before the wait starts still ends it.
- Each kind names the word a wait sleeps on: a box's `wake_word`; for a
  stream, `data_word` for readers and `space_word` for the sender. A wait
  returns when its kind's condition holds, the slot's `interrupt` flag is
  set, or the timeout passes. A box counts the threads inside a wait in
  `sleepers`, a stream the threads asleep on each word in `data_waiting`
  and `space_waiting`; a wake is made only when the count is not 0. Each
  wait names its count in its slot's `asleep_on` as a box's wait does.
  On Windows a wake sets the event of every claimed slot, so the other end
  of a stream may wake and sleep again. `interrupt` on a stream end adds 1
  to that end's word and wakes it on Linux, and sets that slot's event on
  Windows.
- The Python watcher waits in steps of at most 1 s. Writes and `interrupt`
  wake it at once; after each step it checks that its slot still records
  its own `(pid, start, pidns)` and claims a new slot if not. While every
  slot is taken it checks for changes once a second instead of waiting.

### Liveness

A process is named by its pid and its start time.

- Start time: Windows, the creation time from `GetProcessTimes`; Linux,
  field 22 of `/proc/<pid>/stat`, parsed after the last `)`. A real value
  of 0 counts as 1.
- Dead: no process has the pid; or one does and its start time differs
  (the pid was reused); or, on Linux, its state is `Z` or `X`. On Windows
  a process whose handle is signalled has exited.
- Alive: the process exists but its start time cannot be read (another
  user, `/proc` mounted with `hidepid`, access denied on Windows), or the
  recorded start time was the unreadable marker. A slot is never freed on
  a guess.
- Pid namespaces (Linux): a slot records its owner's pid namespace, the
  inode number of `/proc/self/ns/pid`. A slot whose `owner_pidns` differs
  from the checking process's own counts as alive, because its pid cannot
  be checked from there; only its owner, or creating the segment again,
  frees it. This covers containers that share `/dev/shm` but not a pid
  namespace. A process that cannot read its own namespace records 0, which
  means unknown: a slot whose namespace is 0, or a checker whose own is 0,
  is never freed. Windows has no pid namespaces and records 0.
- The current pid is cached, and a `pthread_atfork` handler resets the
  cache in the child after `fork`; the start time and namespace are read
  on first use.

The Python extension uses the same rules for the creator fields: when a
create finds the name taken, `SegmentExistsError` says whether the creator
still runs, runs in another pid namespace, or has exited. For a box not
published yet it reads the creator fields without waiting for `magic`,
and a creator that still runs is reported as still creating the box.

### Send and receive

The sender writes position `p` into slot `p % capacity`, where `p` is
`write_pos`. Only the sender stores `write_pos`.

Send:

1. Gate. With no lossless reader open (`lossless_readers` is 0) the sender
   skips this step. Otherwise it keeps the smallest `position` among the
   entries whose `mode` is lossless, and rescans the reader table when
   `readers_epoch` has changed or when `p` reaches that smallest position
   plus `capacity`. While `p` is at least the smallest position plus
   `capacity`, the ring is full for a lossless reader and the sender waits:
   with a timeout of 0 it gives `status::timeout` at once. Otherwise it
   rescans up to 1000 times (`spin_before_sleep`), then sleeps on
   `space_word` in steps of at most 0.1 s (`liveness_delay`), counting
   itself in `space_waiting` while it sleeps. `status::timeout` follows when
   the timeout passes.
2. Store `seq = 2p + 1`, then a release fence, copy the item, store
   `seq = 2p + 2` with release, and store `write_pos = p + 1` with release.
3. A sequentially consistent fence, then, if `data_waiting` is not 0, add 1
   to `data_word` and wake it.

Receive, for a reader whose next position is `r`:

1. Load `write_pos` with acquire. If it equals `r` there is nothing to
   read. The reader gives `status::ended` if `state` is ended (read before
   `write_pos`, since the sender stores `state` after its last `write_pos`).
   Otherwise, with a timeout of 0 it gives `status::timeout` at once. With
   a longer timeout it checks `write_pos` up to 1000 times, then sleeps on
   `data_word` in steps of at most 0.1 s, counting itself in `data_waiting`
   while it sleeps.
2. A lossy reader with `write_pos - r > capacity` moves to
   `write_pos - capacity`. A latest reader with `write_pos - r > 1` moves to
   `write_pos - 1`. Both count the items they pass as missed.
3. Load `seq`, which must be `2r + 2`, copy the item, issue an acquire
   fence and load `seq` again. If it changed, the sender wrote a later
   position over the slot. A lossy or latest reader counts the item as
   missed and moves on. A lossless reader moves on without counting until it
   has received its first item; after that the gate rules this out, and a
   lossless reader that still sees it gets `status::corrupt`.
4. Store `position = r + 1` in its entry with release. A lossless reader
   then issues a sequentially consistent fence and, if `space_waiting` is
   not 0, adds 1 to `space_word` and wakes it.

A wait that ends with the slot's `interrupt` flag set gives
`status::interrupted` and clears the flag; the flag stays set until a wait
sees it, as for a box.

A reader joins in this order:

1. Free the entries and waiter slots of dead processes (see Dead stream
   ends), then claim a waiter slot. No slot free gives `status::no_slot`.
2. Claim a free entry with a compare-and-swap of `owner_pid` from 0 to this
   process's pid, then store `owner_pidns` and `owner_start`. No entry free
   gives the waiter slot back and `status::no_slot`.
3. Store the starting `position` with release: for `start_at::newest`
   `write_pos - 1`, or 0 before the first send, and for `start_at::oldest`
   `write_pos - capacity`, or 0.
4. If the reader is lossless, add 1 to `lossless_readers`. Then store its
   `mode` with release, add 1 to `readers_epoch`, and issue a sequentially
   consistent fence. The count comes first so that a process killed in
   between leaves it too high, never too low.

A reader leaves only if its entry still records this process: store
`mode = 0`, subtract 1 from `lossless_readers` if it was lossless, add 1 to
`readers_epoch`, then clear `owner_start`, `owner_pidns` and `owner_pid`. A
lossless reader then adds 1 to `space_word` and wakes it, since it may have
been the one holding the sender back. Last it gives back its waiter slot.

The sender claims `sender_pid` with a compare-and-swap from 0 to its pid,
after claiming a waiter slot, and stores `sender_pidns` and `sender_start`.
It gives `status::busy` while another running process holds `sender_pid`.
Closing the sender stores `state = 1` (ended), clears `sender_start`,
`sender_pidns` and `sender_pid`, adds 1 to `data_word`, wakes it, and gives
back the waiter slot. `ended` is never undone: `sender()` on an ended stream
gives `status::ended`, and so does a `sender()` that finds the stream ended
after it claimed `sender_pid`, which it then clears. A reader opened after
the sender closed receives what is buffered, then `status::ended`.

A process created by `fork` inherits its parent's senders and readers but
cannot use them: a send or receive from the child gives `status::range`, and
closing them in the child leaves the parent's entries alone.

### Dead stream ends

A wait never sleeps for more than 0.1 s (`liveness_delay`) at a time. After
each step that ends without a change, it checks the processes it waits for
with the rules of Liveness:

- A sender waiting for space frees the entries of readers whose process has
  exited, with the `start_freeing` exchange that waiter slots use (see
  Waiter slots). For each it sets `mode` to 0, subtracts 1 from
  `lossless_readers` if the mode was lossless, clears the owner, and adds 1
  to `readers_epoch` once for the scan. It then frees the waiter slots of
  dead processes. `reader()` does the same before it claims anything.
- A reader waiting for data ends the stream when the sender's process has
  exited. It first claims the dead sender by changing `sender_start` from the
  value it read to `start_freeing` with a compare-and-swap. If that
  succeeds, it changes `state` from 0 to 1, clears `sender_pidns`,
  `sender_pid` and `sender_start`, adds 1 to `data_word` and wakes it.
  Readers then receive what was published, then `status::ended`.
- `sender()` replaces a recorded sender whose process has exited. It claims
  the dead sender the same way, so a replacement and an ending never both
  act on one dead owner, then stores its own pid.

Known limits:

- Only a wait notices a dead process. A sender that only sends with a
  timeout of 0 never frees a dead lossless reader, and a reader that only
  receives with a timeout of 0 never sees the stream end after its sender
  dies.
- A process killed while it waits, after it adds 1 to `data_waiting` or
  `space_waiting` and before it stores the count's offset in its slot's
  `asleep_on`, leaves that count one too high for good. That costs a wake
  call on each send or receive; no wake is lost. Killed later in the wait,
  before its exit exchange of `asleep_on`, it leaves the offset, and freeing
  its waiter slot takes the 1 back. Killed between that exchange and its
  subtraction, it leaves the count one too high. A freer of its slot killed
  after it won the slot and before its subtraction leaves the count one too
  high and the slot stuck with the marker.
- A process killed in the few instructions between setting `sender_start` to
  `start_freeing` and its next store leaves `sender_start` at
  `start_freeing`. Every later `sender()` then gives `status::busy`, and
  readers wait until their timeout instead of getting `status::ended`. The
  waiter slots have the same limit.
- A process killed while it frees a reader entry leaves the entry unusable
  until the stream is created again.
- A waiting end spins for up to 1000 checks before it sleeps, which uses a
  CPU for that time whenever the timeout is above 0.

### Close and unlink

- Destroying a handle releases any waiter slot it holds, then unmaps its
  view and closes its OS handles, including the events it opened.
- `unlink(name)`: Linux `shm_unlink("/SBX:<name>")`; Windows does
  nothing.

## Implementation language

The library is C++20, header-only, in namespace `sharedbox`. `core.hpp`
holds what every kind of segment uses: results and errors, liveness, names,
mappings, the open step, waiter slots and the type codec. `box.hpp` holds
the box: its layout, the protocols and `handle`. `stream.hpp` holds the
stream: its layout, the protocols and `stream`, `stream_sender` and
`stream_reader`. `sharedbox.hpp` includes every kind and is the
header consumers include. Names are declared in the inline namespace
`sharedbox::v3`, which changes when the
C++ interface changes incompatibly, so code built against headers with
different inline namespaces can be linked into one program. Names in
`sharedbox::detail` may change without a new inline namespace, so shared
libraries build with hidden visibility to keep their copies apart. C
programs use it through `sharedbox_c.h`, whose `sbx_*` functions are not
versioned this way and are declared with hidden visibility outside
Windows.

- The layout structs are plain standard-layout types with integer members.
- Atomics: `std::atomic_ref` on those members. `static_assert`s require
  `std::atomic_ref<std::uint32_t>` and `std::atomic_ref<std::uint64_t>` to
  be always lock-free, because only lock-free atomics work on memory shared
  between processes; a fallback that takes a lock would keep that lock in
  one process. `std::atomic_ref::wait` and `notify_*` are not used, since
  implementations do not promise they work across processes, and
  `std::atomic` members are not used because the standard does not fix
  their size or layout. With `std::atomic_ref` the compiler emits the
  barriers each CPU needs, with one code path for every compiler.
- 64-bit little-endian targets only; the header refuses a big-endian
  target at compile time.
- No allocation on the read and write paths. No exceptions: every function
  that can fail returns `result<T>`, a value or an `error`. `result<T>` is
  the library's own class on C++20 and C++23 alike, with the part of the
  interface of `std::expected<T, error>` it needs (`has_value`,
  `operator bool`, `operator*`, `->`, `value`, `error`, `and_then`,
  `transform`, `or_else`, `value_or`); `value()` on an error calls
  `std::terminate`. An `error` holds the `status` (`code`), the OS error
  (`os`, the `errno` or `GetLastError()` value read where the OS call
  failed) and what the call found (`found`, see Attach). Every function
  returning a `result` is `[[nodiscard]]`, and the header builds with
  `-fno-exceptions`.
- Where the standard library has `std::expected` with
  `__cpp_lib_expected >= 202211L` (C++23), the free functions
  `to_expected(result<T>)` and `from_expected(std::expected<T, error>)`
  convert one into the other. Because `result` is never an alias and its
  definition does not depend on the C++ version, translation units built as
  C++20 and as C++23 can be linked into one program.

## `sharedbox.hpp`

```cpp
namespace sharedbox {
inline namespace v3 {

inline constexpr std::uint16_t core_major = 1, core_minor = 0;
inline constexpr std::uint16_t layout_major = 3, layout_minor = 0;   // the box layout
inline constexpr std::uint64_t box_magic = 0x5F584F425F584253;       // "SBX_BOX_"
inline constexpr std::uint64_t stream_magic = 0x4D5254535F584253;    // "SBX_STRM"
// Also: the layout constants handle_version, header_size, name_max, max_fields,
// max_capacity, max_waiter_slots, default_waiter_slots, record_alignment, page_size,
// max_timeout, default_lock_timeout, kind_shift, capacity_mask, max_types_size,
// max_type_depth, max_mapping_size, first_described_kind, max_date_ordinal,
// unix_epoch_ordinal, micros_per_day, max_offset_minutes, literal_none through
// literal_enum, and the kind codes kind_bool, kind_int, kind_float, kind_str,
// kind_bytes, kind_ref, kind_complex through kind_decimal and kind_enum through
// kind_array; the structs common_header, header, stored_field, waiter_slot, box_ref and
// type_head (see Layout); dl_dtype and literal_value, which type_view's dtype()
// and literal() return (see the list below); error, result<T> and unexpected (see
// Implementation language).

enum class status : int { ok = 0, exists = -1, not_found = -2, layout = -3, schema = -4,
                          corrupt = -5, lock_timeout = -6, timeout = -7, no_slot = -8,
                          range = -9, os = -10, kind_mismatch = -11, foreign = -12,
                          busy = -13, ended = -14, interrupted = -15 };

struct field_spec { std::uint32_t offset; std::uint32_t capacity; std::uint8_t kind; };
struct value { std::uint16_t field; std::span<const std::byte> bytes; };
using seconds = std::chrono::duration<double>;
struct read_value { std::size_t len; std::uint64_t version; };
enum class wake { changed, interrupted };      // a timeout is status::timeout

class handle {                                  // move-only; the destructor releases it
public:
    static result<handle> create(std::string_view name, std::span<const field_spec> fields,
                                 std::uint32_t record_size, std::uint64_t schema_hash,
                                 std::uint16_t waiter_slots, std::span<const value> initial,
                                 std::span<const std::byte> types = {});
    static result<handle> create_unpublished(std::string_view name, std::span<const field_spec> fields,
                                             std::uint32_t record_size, std::uint64_t schema_hash,
                                             std::uint16_t waiter_slots, std::span<const value> initial,
                                             std::span<const std::byte> types = {});
    result<void> publish() noexcept;
    static result<handle> open(std::string_view name, seconds timeout);
    static result<handle> from_capsule(sbx_handle *capsule);
    result<handle> duplicate() const;
    sbx_handle *to_capsule() &&;

    std::string_view name() const noexcept;
    std::uint16_t field_count() const noexcept;
    const field_spec &field(std::uint16_t index) const noexcept;
    type_view field_type(std::uint16_t index) const noexcept;
    std::span<const std::byte> types_table() const noexcept;
    std::uint32_t record_size() const noexcept;
    std::uint64_t schema_hash() const noexcept;
    std::uint64_t create_id() const noexcept;
    std::uint16_t waiter_slots() const noexcept;
    std::uint16_t minor_version() const noexcept;
    std::uint16_t major_version() const noexcept;
    void *base() const noexcept;
    std::uint64_t size() const noexcept;

    result<void> set_lock_timeout(seconds timeout) noexcept;
    result<read_value> read(std::uint16_t field, std::span<std::byte> buf) const;
    result<std::uint64_t> read_record(std::span<std::byte> buf) const;
    result<read_value> read_used(std::uint16_t field, std::span<std::byte> buf) const;
    result<read_value> read_large(std::uint16_t field, std::span<std::byte> buf) const;
    result<std::uint64_t> read_record_large(std::span<std::byte> buf) const;
    result<void> write_large(std::span<const value> values, seconds lock_timeout);
    std::span<const std::byte> payload(std::uint16_t field, std::span<const std::byte> record) const noexcept;
    result<void> write(std::span<const value> values, seconds lock_timeout);
    std::uint64_t generation() const noexcept;
    std::uint64_t version(std::uint16_t field) const noexcept;
    std::uint32_t writer_pid() const noexcept;
    result<void> force_unlock() noexcept;

    result<std::uint16_t> register_waiter();
    void release_waiter(std::uint16_t slot) noexcept;
    bool waiter_held(std::uint16_t slot) const noexcept;
    std::uint32_t waiters() const noexcept;
    std::uint32_t sleepers() const noexcept;
    result<wake> wait(std::uint16_t slot, std::uint64_t last_generation, seconds timeout);
    result<void> interrupt(std::uint16_t slot);
};

result<void> unlink(std::string_view name) noexcept;
result<common_header> inspect(std::string_view name) noexcept;

}  // namespace v3
}  // namespace sharedbox
```

Every function returning `result` is `[[nodiscard]]`. `handle` also has
`lock`, `unlock` and `set_wait_hooks`, which the tests and the Python
extension use.

The header also declares these names:

- `type_view` and `handle::field_type(index)`: a field's type, read from
  its description. `handle::types_table()` returns the table's bytes.
- `time_value`, `datetime_value` and `timedelta_value`, with an
  `encode_*` and a `decode_*` function for each, and the same pair for
  `complex`, `date`, `uuid`, `flag` and enum and literal positions
  (`encode_position`, `decode_position`). `decode_bool`, `decode_present`,
  `decode_tag` and `decode_length` have no encoder.
- `handle::major_version()`, the box layout major of the segment.
- The constants `first_described_kind`, `max_date_ordinal`,
  `unix_epoch_ordinal`, `micros_per_day`, `max_offset_minutes` and
  `literal_none` through `literal_enum`, and the structs `type_head`,
  `dl_dtype` and `literal_value`.
- `handle::read_used`, which copies only the used part of a list, set or
  dict field; `read_large`, `read_record_large` and `write_large`, which
  copy a large value, running the wait hooks once around the copy.
- A `types` parameter on `handle::create` and `handle::create_unpublished`:
  the description table, empty by default.
- The inline namespace is `v3`.

- `status`: `ok`, and one code per error the Python side raises, so the
  extension maps each to its exception class. `exists`: the name is taken.
  `not_found`: no mapping under the name, or one that never becomes a box
  within the timeout. `layout`: another `core_major` or box `kind_major`.
  `kind_mismatch`: the name holds a segment of another kind.
  `foreign`: the magic is not one this library knows, so the segment was
  made by another version of `sharedbox` or is not a `sharedbox` segment.
  `schema` is kept for callers that compare schema hashes. `corrupt`: a header or field
  table that fails the attach checks. `lock_timeout`, `timeout`, `no_slot`:
  see Protocols. `range`: an argument out of range. `os`: an OS call
  failed, and the error's `os` holds its `errno` or `GetLastError()`.
  `busy`: the sender is held by a running process. `ended`: the stream's
  sender closed or died and every published item was received.
  `interrupted`: a stream wait ended by `interrupt()`.
- `create` and `write` take raw bytes in the record encoding, without the
  length prefix of `str`, `bytes` and `Decimal`; converting language values
  stays in each binding.
- `open` takes no schema hash; callers compare `schema_hash()` with their
  own. Its `timeout`, in `(0, 86400]`, only bounds the wait for a creator
  (the Python extension passes at most 1 s). Reads use a lock timeout of
  5 s until `set_lock_timeout` changes it; `write` takes its own.
- `read` returns `read_value`. When the stored value is longer than `buf`,
  nothing is copied and `len` says how long it is; a buffer of the field's
  capacity always fits. `read_record` copies every field from one moment
  and returns the generation of that moment; `payload` finds a field in
  such a copy.
- `inspect(name)` copies the common line of a published segment of any
  kind, without waiting and without the checks of `open`. The Python
  extension uses it to name the layout of a segment it refuses and the
  creator in `SegmentExistsError`.
- `duplicate()` makes a second handle with its own mapping from this
  handle's OS handle (`dup` and `mmap` on Linux, `DuplicateHandle` and
  `MapViewOfFile` on Windows), not from the name, so it works after
  `unlink()`.
- `minor_version()` is the lower of the segment's `kind_minor` and the
  `layout_minor` of the `box.hpp` the caller was compiled with.
- Shared memory that never becomes a box within the timeout is
  `status::not_found`.
- Thread safety: every member function may be called from several threads
  on one handle. Destroying or moving a handle must not overlap another
  call on it.
- Known limit: on Linux every open handle keeps the mapping's file
  descriptor, so about 1000 open boxes reach the default `ulimit -n` of
  1024.

### `stream.hpp`

```cpp
inline constexpr std::uint16_t stream_layout_major = 1, stream_layout_minor = 0;
inline constexpr std::uint32_t stream_header_size = 256;
inline constexpr std::uint64_t min_stream_capacity = 2;
inline constexpr std::uint32_t max_stream_readers = max_waiter_slots - 1;   // 4095
inline constexpr std::uint64_t max_stream_size = std::uint64_t{1} << 46;
inline constexpr std::uint32_t stream_open = 0, stream_ended = 1;
inline constexpr double liveness_delay = 0.1;       // seconds a wait lasts before it checks for dead processes
inline constexpr unsigned spin_before_sleep = 1000; // checks a waiting end makes before it sleeps

enum class read_mode : std::uint32_t { lossless = 1, lossy = 2, latest = 3 };
enum class start_at { newest, oldest };

struct received { std::uint64_t position; std::uint64_t missed; };
struct reader_info { std::uint64_t position; read_mode mode; std::uint32_t pid; };

class stream_sender {                       // move-only; the destructor closes it
public:
    std::uint64_t item_size() const noexcept;
    result<std::uint64_t> send(std::span<const std::byte> item, seconds timeout);
    template <class F> requires std::is_nothrow_invocable_v<F &, std::span<std::byte>>
    result<std::uint64_t> send_with(F &&fill, seconds timeout);
    result<void> interrupt();
    void close() noexcept;
};

class stream_reader {                       // move-only; the destructor closes it
public:
    read_mode mode() const noexcept;
    std::uint64_t position() const noexcept;
    std::uint64_t missed() const noexcept;
    std::uint64_t item_size() const noexcept;
    result<received> receive(std::span<std::byte> out, seconds timeout);
    template <class F> requires std::is_nothrow_invocable_v<F &, std::span<const std::byte>>
    result<received> receive_with(F &&copy, seconds timeout);
    result<void> interrupt();
    void close() noexcept;
};

class stream {                              // move-only; the destructor releases it
public:
    static result<stream> create(std::string_view name, std::span<const std::byte> types,
                                 std::uint32_t item_entry, std::uint64_t capacity,
                                 std::uint32_t max_readers, std::uint64_t schema_hash);
    static result<stream> open(std::string_view name, seconds timeout);

    std::string_view name() const noexcept;
    std::uint64_t schema_hash() const noexcept;
    std::uint64_t create_id() const noexcept;
    std::uint64_t capacity() const noexcept;
    std::uint32_t max_readers() const noexcept;
    std::uint64_t item_size() const noexcept;
    type_view item_type() const noexcept;
    std::span<const std::byte> types_table() const noexcept;
    void *base() const noexcept;
    std::uint64_t size() const noexcept;
    result<stream_sender> sender();
    result<stream_reader> reader(read_mode mode, start_at start);
    std::uint64_t write_position() const noexcept;
    bool ended() const noexcept;
    std::uint32_t sender_pid() const noexcept;
    std::size_t readers(std::span<reader_info> out) const noexcept;
};
```

The header also declares the structs `stream_header` and `reader_entry` (see
Stream layout). Every function returning a `result` is `[[nodiscard]]`.

- `stream::create` takes the item as an `item_entry`, a `capacity_and_kind`
  word as a field table entry has, and the description table `types` that
  entry refers to. `status::range` for a name that breaks the rules, a
  `capacity` below 2, a `max_readers` outside 1 to 4095, a table that does
  not parse or an item of an unknown kind, or a shape whose slots would
  pass 2^46 bytes; `status::exists` if the name is taken.
- `stream::open` compares nothing: the caller compares `schema_hash()` with
  its own. Its errors are those of Attach, and `status::corrupt` for an
  item type or slot size that does not fit.
- `sender` gives `status::busy` while a running process holds the sender,
  `status::ended` once a sender has closed the stream, and `status::no_slot`
  when no waiter slot is free.
- `reader` gives `status::no_slot` when `max_readers` readers are open or no
  waiter slot is free.
- `send` copies `item_size()` bytes into the next slot and returns its
  position. `send_with` calls `fill` with the slot's `item_size()` bytes
  instead; `fill` must not throw or call the sender. Both give
  `status::timeout` while a lossless reader is a full ring behind (at once
  for a timeout of 0), `status::interrupted` after `interrupt()`, and
  `status::range` for a sender that is closed, used from a child of `fork`
  or given an item of the wrong size.
- `receive` copies the next item into `out` and returns its position and the
  number of items this reader skipped since its previous receive (always 0
  for a lossless reader). `receive_with` calls `copy` with the item's bytes
  in the slot; the sender may change them during the call, so `copy` must
  only copy and must not throw. `status::timeout` when no item arrives in
  time (at once for 0), `status::ended` once the sender has closed or died
  and every published item was received, `status::interrupted` after
  `interrupt()`, and `status::range` for an `out` shorter than `item_size()`.
- `interrupt` ends the wait in progress with `status::interrupted`. Sent
  while nothing waits, it ends the next wait. It must not overlap `close`.
- `close` on the sender ends the stream for good. `close` on a reader frees
  its entry.
- `write_position()` is the number of items sent. `readers(out)` fills `out`
  with the open readers, as many as fit, and returns how many are open.
- Calls on one sender or one reader must not overlap, except `interrupt`.
  Senders and readers must not outlive the `stream` that made them.

### Capsule handle

`sbx_handle` is the struct a capsule holds. It is plain C data with function
pointers, declared in `sharedbox_c.h` and included by `box.hpp`,
because it passes between extensions compiled separately, possibly with
different compilers and standard libraries. No C++ standard-library type
crosses the capsule.

```c
typedef struct sbx_handle {
    uint16_t layout_major, layout_minor;
    uint32_t handle_version;              /* 1; grows by appending fields */
    void    *base;                        /* this handle's own mapping */
    uint64_t size;
    const char *name;                     /* the box name, for slot events */
    void   (*release)(struct sbx_handle *);
    void    *private_data;                /* owned by the build that made the handle */
} sbx_handle;
```

- `private_data` belongs to the build that made the handle, and only that
  build's `release` reads it.
- A consumer never reads another build's `private_data`. It takes the
  handle with `handle::from_capsule` (or `sbx_import`). It does not wait
  for `magic`: it loads it once and returns `status::corrupt` if it is not
  set or `size` is below 4096, then runs steps 3 and 4 of Attach on the
  segment at `base`, which includes the kind and version checks. It keeps its own copy of the struct and of the field table, and
  checks no schema: the consumer compares `schema_hash()`. On success it
  clears the capsule struct's `release`, and destroying the new handle
  calls the producer's `release`. The consumer's handle opens slot events
  by `name` when it first waits.
- `to_capsule()` moves a handle to the heap and returns a new `sbx_handle`
  whose `release` destroys it; the caller deletes the struct afterwards.
- The Python extension's capsule destructor deletes the struct while the
  capsule is named `"sharedbox_box"` or `"used_sharedbox_box"`, and calls
  `release` only under the first name.

## `sharedbox_c.h`

`include/sharedbox/sharedbox_c.h` is a minimal C99 interface, documented as
"minimal, may be removed in a future major version". Removing it would only
delete code: a semver major step for the headers, with no change to the
layout.

```c
#define SBX_OK 0                 /* SBX_E_* have the values of sharedbox::status */
typedef struct sbx_value { uint16_t field; const void *data; size_t len; } sbx_value;

int      sbx_open(const char *name, double timeout, sbx_handle *out);
int      sbx_import(sbx_handle *capsule, sbx_handle *out);
int      sbx_read(const sbx_handle *h, uint16_t field, void *buf, size_t cap,
                  size_t *len, uint64_t *version);
int      sbx_write(sbx_handle *h, const sbx_value *values, size_t n, double lock_timeout);
uint64_t sbx_schema_hash(const sbx_handle *h);
void     sbx_release(sbx_handle *h);
```

- It holds `sbx_handle`, `sbx_value`, the status codes, the six functions
  above and the typed functions below. Creating, waiting, `interrupt`,
  `force_unlock` and `unlink` are in the C++ API only.
- The status codes are `SBX_E_EXISTS` (-1) to `SBX_E_OS` (-10), then
  `SBX_E_KIND` (-11) and `SBX_E_FOREIGN` (-12), the values of
  `status::kind_mismatch` and `status::foreign`, and `SBX_E_BUSY` (-13),
  `SBX_E_ENDED` (-14) and `SBX_E_INTERRUPTED` (-15), the values of
  `status::busy`, `status::ended` and `status::interrupted`. Streams have no
  C functions. `sbx_open` and `sbx_import` return `SBX_E_KIND` for a segment of another kind and
  `SBX_E_FOREIGN` for a magic they do not know (see Attach). A C caller
  gets the code only, without the `error`'s OS error and `found`.
- `sbx_field_desc` returns a field's kind code and its description's
  bytes. `sbx_read_*` and `sbx_write_*` exist for `complex`, `date`,
  `time`, `datetime`, `timedelta`, `uuid`, enum and literal positions
  (`position`), flag bits (`flag`) and an optional's presence (`present`,
  read only). A read gives `SBX_E_RANGE` for a field of another kind and
  `SBX_E_CORRUPT` for a stored value no Python value has; a write gives
  `SBX_E_RANGE` for a value out of range.
- `sbx_time` and `sbx_datetime` hold `int64_t micros`, `int16_t
  offset_minutes` and `uint8_t naive, fold`; `sbx_timedelta` holds
  `int32_t days, seconds, microseconds`. A `sbx_time` counts microseconds
  since midnight in wall-clock time; a `sbx_datetime` counts microseconds
  since 1970-01-01T00:00, wall time when naive and UTC when aware.
- Each function converts its arguments and calls `sharedbox.hpp`.
  `sbx_open` fills `out` with a handle whose `private_data` holds a
  `sharedbox::handle` and whose `release` deletes it. `sbx_import` wraps a
  capsule's handle through `handle::from_capsule`.
- `sbx_read`, `sbx_write` and `sbx_schema_hash` accept only handles made
  by `sbx_open` or `sbx_import`; for any other, `sbx_read` and `sbx_write`
  return `SBX_E_RANGE` and `sbx_schema_hash` returns 0. `sbx_write` also
  returns `SBX_E_RANGE` for a field of a kind the header does not know. When the stored
  value is longer than `cap`, `sbx_read` copies nothing, sets `*len` and
  returns `SBX_E_RANGE`. `sbx_release` releases any handle.
- The implementation, `include/sharedbox/sharedbox_c.cpp`, needs a C++20
  compiler: a C program compiles that one file with its C++ compiler next
  to its own sources, and the CMake target `sharedbox::c` adds it for
  them. One source file rather than a static library in the wheel,
  because a static library is tied to the compiler and C runtime that
  built it.

## The PyCapsule interface

```python
class Frame(SharedBox, identity="camera/frame/1", max_waiters=64):
    exposure: float
    count: int


with Frame(0.01, 0) as frame:
    camera.run(frame)  # an extension that supports sharedbox
```

The dunder is a protocol for library authors, as `__arrow_c_array__` and
`__dlpack__` are: Python users pass the box to a consuming library, which
calls it. A capsule is an opaque pointer Python code cannot use, so there is
no Python-level export method.

- `SharedBox.__sharedbox_box__(self, max_version=None, **kwargs)` returns
  a PyCapsule named `"sharedbox_box"` holding a heap-allocated
  `sbx_handle` made by `duplicate()` and `to_capsule()`, so it works after
  `unlink()`.
- `max_version` is `(major, minor)`; a major the box does not have raises
  `BufferError`, and `None` means the current version. Any other keyword
  raises `NotImplementedError`.
- A consumer that takes the handle renames the capsule
  `"used_sharedbox_box"` and releases the handle itself.
- `release` unmaps and closes OS handles only: no Python objects, no GIL,
  so it may run on any thread and after interpreter shutdown. `box.close()`
  and the handle never affect each other. On Windows the segment stays
  alive while a handle is held.
- `sharedbox.get_include()` returns the folder holding
  `sharedbox/sharedbox.hpp`, `sharedbox/sharedbox_c.h` and
  `sharedbox/sharedbox_c.cpp` in the installed package.
- `sharedbox.SupportsSharedBox` is a `typing.Protocol` with
  `__sharedbox_box__`, for annotations in consuming libraries.
- `sharedbox._native.LAYOUT_VERSION` is `(3, 0)`, the box layout.

## Rust crate (deferred)

A Rust crate is not part of this release. It will be a safe layer over
the headers through cxx, so Python, C++ and Rust share one
implementation of the protocols, with `schema_hash` and the default name
computed in Rust and checked against the test vector above.

## Build and packaging

- `include/sharedbox/core.hpp`, `box.hpp`, `stream.hpp`, `sharedbox.hpp`,
  `sharedbox_c.h` and `sharedbox_c.cpp` are in the repository; the build
  installs them into the wheel under `sharedbox/include/`, and
  `cmake/sharedbox-config.cmake` under `sharedbox/share/cmake/sharedbox/`,
  next to a
  `sharedbox-config-version.cmake` the build writes from the package
  version. Before 1.0 it accepts a request for the same minor version
  only (`SameMinorVersion`), from 1.0 on the same major version.
- CMake targets: `sharedbox::headers`, an interface target that requires
  `cxx_std_20` (and links `rt` and `Threads::Threads` on Linux, `bcrypt`
  on Windows), and
  `sharedbox::c`, which links `sharedbox::headers` and adds
  `sharedbox_c.cpp` to the sources of whatever links it. Both work through
  `FetchContent` and through `find_package(sharedbox)`. A project that
  links `sharedbox::c` without the CXX language enabled stops at configure
  time with a message saying so.
- A Python extension that uses the header adds `sharedbox` to its
  `[build-system] requires`, calls `sharedbox.get_include()` and builds as
  C++20. The sharedbox extension itself builds as C++20.

## Out of scope

- Queues, which would get `__sharedbox_queue__`.
- A Python class, a capsule (`__sharedbox_stream__`) and C functions for
  streams. A stream is C++ only in this version.
- `Global\` names on Windows. They would let a service in session 0 share
  a box with a desktop application, but creating a file mapping there
  needs `SeCreateGlobalPrivilege` and a security descriptor that lets the
  other session's user open it.
- A vcpkg port and a headers-only wheel.
- macOS. Every platform detail lives in `core.hpp` and `box.hpp`, so macOS
  would be a third platform there. Known obstacles: POSIX shared memory names are
  limited to 31 characters, which `SBX:<name>` with names of up to 240
  characters does not fit; there is no futex; there is no
  `posix_fallocate`; leftover segments cannot be listed.
- A read-only open mode.
- Big-endian hosts.

## References

- Arrow PyCapsule Interface:
  https://arrow.apache.org/docs/format/CDataInterface/PyCapsuleInterface.html
- DLPack Python specification (`__dlpack__`, `max_version`, capsule
  renaming): https://dmlc.github.io/dlpack/latest/python_spec.html
- Ulrich Drepper, "Futexes Are Tricky":
  https://www.akkadia.org/drepper/futex.pdf
- psutil, process start time as identity:
  https://github.com/giampaolo/psutil
- Microsoft, kernel object namespaces (`Local\`, `Global\`):
  https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces
