# Segment layout 1.0

This page describes how a box is stored in shared memory: the object names,
the bytes of the mapping, and the protocols every process follows to read,
write and wait. It is the contract between sharedbox and any other code that
opens a box. `include/sharedbox/sharedbox.hpp` implements it, and the Python
extension runs on that header.

## Goal

C++ and C code can use a sharedbox box, both as an extension loaded in a
Python process and as a standalone program with no Python, without linking
a library that sharedbox ships. The layout and protocols below are
versioned, and one header-only C++20 library implements them.

## Decisions

| Topic | Decision | Not taken |
| --- | --- | --- |
| Model | the Arrow PyCapsule Interface; the closest analogue is `ArrowArrayStream`, a live handle rather than a one-shot transfer | numpy's `__array_struct__` |
| Scope | boxes only; the handle struct is versioned so that queues and streams can get their own dunders later | queues and streams in the same step |
| Contract | a documented memory layout plus a header-only reference implementation | a compiled C library; definitions only |
| Language | the C++20 header `sharedbox.hpp`, namespace `sharedbox` | a C99 header with `static inline` functions |
| Atomics | `std::atomic_ref` on plain integer fields | wrappers over compiler intrinsics; C11 `<stdatomic.h>` |
| C interface | a minimal `sharedbox_c.h`, whose functions call `sharedbox.hpp`, in one `.cpp` file the consumer compiles | a static library in the wheel; a C implementation of the protocols |
| Dunder and capsule | `__sharedbox_box__`, capsule `"sharedbox_box"` | `_c_` in the name, which reads as "implemented in C" |
| Negotiation | DLPack's `max_version`; the consumer renames the capsule `"used_sharedbox_box"`; other keywords raise `NotImplementedError`, as in Arrow | none |
| Object names | `sharedbox.` + box name + suffix, for every object | the bare box name; names stored in the header |
| Windows scope | `Local\` only | `Global\` (see Out of scope) |
| Versioning | `layout_major` refuses, `layout_minor` opens and ignores what it does not know | exact match; flag masks |
| Capsule lifetime | the handle maps the segment again from the box's OS handle | sharing the box's mapping and keeping the box alive |
| Schema identity | `identity=` class keyword, default `module.qualname`; it enters the schema hash and names the box; `handle::create` is available to other languages | the Python module path written into other languages; open-only for other languages |
| Distribution | headers in the wheel, `sharedbox.get_include()` and a CMake config | a vcpkg port or a headers-only wheel |

## Names

Every OS object of a box is named from the box name:

| Object | Linux | Windows |
| --- | --- | --- |
| mapping | `shm_open("/sharedbox.<name>")`, seen as `/dev/shm/sharedbox.<name>` | `CreateFileMappingW(INVALID_HANDLE_VALUE, ..., L"Local\\sharedbox.<name>")` |
| wake word | inside the mapping, no name | not used |
| waiter slot `i` | inside the mapping, no name | auto-reset event `Local\sharedbox.<name>.w<i>` |

- A box name matches `[A-Za-z0-9_.-]{1,128}`.
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

The mapping holds, from offset 0:

```text
offset 0     header            128 bytes, two cache lines
offset 128   field table       field_count x 8 bytes
             write counts      field_count x 8 bytes
             waiter slots      waiter_slots x 24 bytes
             (zero padding up to a multiple of 64)
record       record            record_size bytes, 64-byte aligned
             (zero padding up to a multiple of 4096)
```

No structure has implicit padding: gaps are explicit `pad` and `reserved`
members, and `static_assert`s in `sharedbox.hpp` pin each structure's
`sizeof`, `alignof` and every `offsetof`, and that it is standard-layout
and trivially copyable.

```cpp
struct header {                     // sharedbox::header, 128 bytes
    // line 0: written once at creation
    uint64_t magic;                 //  0  "SHREDBX1"; written last, with release ordering
    uint16_t layout_major;          //  8
    uint16_t layout_minor;          // 10
    uint16_t field_count;           // 12  1 to 256
    uint16_t waiter_slots;          // 14  1 to 4096
    uint64_t schema_hash;           // 16
    uint32_t record_size;           // 24
    uint32_t record;                // 28  offset of the record
    uint32_t tail;                  // 32  offset of the field table, always 128
    uint32_t size;                  // 36  mapping size in bytes
    uint64_t create_id;             // 40  random at create, never 0
    uint64_t creator_start;         // 48  creator's start time, see Liveness
    uint32_t creator_pid;           // 56
    uint8_t  reserved0[4];          // 60  zero; minor versions may use it
    // line 1: changed by writes and waits
    uint64_t seq;                   // 64  sequence lock: even when free, odd while writing
    uint32_t writer_pid;            // 72  holder of the write lock, 0 if none
    uint32_t wake_word;             // 76  futex word (Linux)
    uint32_t waiters;               // 80  occupied waiter slots
    uint8_t  pad0[4];               // 84  zero
    uint64_t creator_pidns;         // 88  creator's pid namespace; 0 on Windows
    uint8_t  reserved1[32];         // 96  zero; minor versions may use it
};
```

Line 0 is written once at creation and read once by attach, which copies
it. Line 1 holds what writes change, so a write touches one header line.
`creator_pidns` sits in line 1 only because line 0 has no room left; it is
written once at creation like the other creator fields.

The header is 128 bytes so that minor versions can add fields: a 64-byte
header has no spare byte, and every addition would move the fields after
it. There is one header per box, and readers and writers touch only line 1.
The waiter slots, not the header, are the large part of the tail, and
`max_waiters` sizes them. Worked example, a box with an `int`, a `float`
and a `bool` and the default 64 waiter slots: header 128, field table 24,
write counts 24, waiter slots 1536, padding 16, record 24 (the Python side
rounds a record up to a multiple of 8): 1752 bytes used, one 4 KiB page. A
box with three fields and 64 slots stays in one page up to about 2.3 KB of
record.

- This is layout `1.0`: bytes 8 to 11 are `01 00 00 00` (`layout_major`
  1, `layout_minor` 0). The layout version counts the segment format and
  is not tied to the package version.
- Field table entry, 8 bytes: `u32 offset`, `u32 capacity_and_kind` (low
  24 bits the capacity, top 8 bits the kind code: `bool` 0, `int` 1,
  `float` 2, `str` 3, `bytes` 4).
- Offsets are the creator's choice. The Python side packs fields by
  descending alignment; another creator may pack differently. Attachers
  read offsets from the field table and never compute them, so a box
  created elsewhere with its own packing reads correctly from Python as
  long as the field order, names, kinds and capacities match, which the
  schema hash checks.
- Write counts: one `u64` per field, incremented under the write lock.
- Waiter slot, 24 bytes:

  ```cpp
  struct waiter_slot {              // sharedbox::waiter_slot
      uint64_t owner_start;         // process start time, see Liveness
      uint64_t owner_pidns;         // pid namespace, see Liveness; 0 on Windows
      uint32_t owner_pid;           // 0 = free
      uint32_t interrupt;           // set by interrupt(), cleared by the waiter
  };
  ```

- Record encoding, little-endian: `bool` 1 byte, `0x00` or `0x01`; `int`
  8 bytes signed; `float` 8 bytes IEEE 754 double; `str` and `bytes` a
  `u32` length, then up to `capacity` bytes (UTF-8 for `str`). Alignment
  within the record: 8 for `int` and `float`, 4 for `str` and `bytes`, 1
  for `bool`.

## Schema identity

The schema is the list of fields that fixes a record's shape: names, kinds,
capacities and their order. The schema hash shows that a creator and an
attacher agree on it and on the class's meaning.

- Hash text: the identity, then one `name:kind:capacity` per field in
  declaration order (base class fields first), joined with `|`, encoded as
  UTF-8. Kinds are the words `bool`, `int`, `float`, `str`, `bytes`;
  capacity is the payload size in bytes (8 for `int` and `float`, 1 for
  `bool`).
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
  ```

## Versioning rules

- A reader refuses a segment whose `layout_major` differs from its own.
- A reader opens a segment whose `layout_minor` is higher than its own, and
  ignores what it does not know. A handle reports the lower of the
  segment's minor and its own.
- A minor version may only add: fields in bytes that are reserved and zero
  in older minors, or features that stay off unless the segment says they
  are on and the reader knows them.
- A change to how existing bytes are read or written (the sequence lock,
  field encoding, the slot layout, object names) raises `layout_major`.
- The package takes a semver major step whenever `layout_major` changes.

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
2. Write the field table, the creator fields and the rest of line 0
   except `magic`.
   `create_id` comes from the OS random source (`getrandom` on Linux,
   falling back to `/dev/urandom` where it is missing or refused;
   `BCryptGenRandom` on Windows) and is drawn again if 0. The creator
   fields are this process's pid, start time and pid namespace. Every
   waiter slot is free because the pages are zero.
3. Write the initial field values into the record.
4. Store `magic` with release ordering. Until then no attach succeeds.
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
3. Check `layout_major` (Versioning rules).
4. Check every geometry field against the mapping size before reading
   through it: `field_count` and `waiter_slots` in range, `tail == 128`,
   the record 64-byte aligned, after the waiter slots and inside the
   mapping, `size` equal to the mapping size, and each field table entry
   (kind known, capacity 1 byte to 1 MiB and 1 or 8 bytes for the fixed
   kinds, alignment, the field inside the record, no two fields
   overlapping). A failed check is `status::corrupt`.
5. Copy the field table and use only the copy afterwards.
6. Free the waiter slots of dead processes (see Waiter slots).

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
  decrements `waiters` if the value it read was nonzero, stores
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
- A thread waits in a slot it holds. The Python watcher registers one slot
  for its lifetime, and a one-off `Segment.wait()` without a slot claims
  one for the call.

Known limits, each after a process is killed at one specific step:

- A freer killed while holding the marker, before it clears `owner_pid`,
  leaves the slot unusable until the segment is created again, and
  `waiters` one too high if it was killed before its decrement.
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

A count that is too high costs a system call on each write; it never loses
a wake-up.

### Wait and wake

- Wait (slot `i`, last seen generation `g`, timeout):
  - Load `wake_word`, then return at once if `seq >> 1 != g` or slot `i`'s
    `interrupt` is set (clearing it with a compare-and-swap).
  - Linux: `FUTEX_WAIT` (shared, not private) on `wake_word` with the value
    loaded before the check, so a write in between makes the call return
    at once.
  - Windows: wait on event `i`, which the waiter's process opens on first
    use and keeps.
  - Check again after waking; a wake may be spurious. Past the timeout the
    result is `status::timeout`.
- Wake, after every write:
  - Increment `wake_word` with sequentially consistent ordering, so the
    following load of `waiters` cannot move before it or before the swap
    of `seq`; a waiter that registered just before would otherwise be
    missed.
  - If `waiters` is 0, stop: no system call on the normal path.
  - Linux: one `FUTEX_WAKE` with `INT_MAX` waiters.
  - Windows: `SetEvent` on the event of every occupied slot.
- `interrupt(slot)`: set the slot's `interrupt`, then wake that slot. On
  Linux it increments `wake_word` and calls `FUTEX_WAKE`, and the other
  waiters check their own flag and sleep again; on Windows it sets that
  slot's event only. The flag stays set until the waiter sees it, so an
  interrupt sent before the wait starts still ends it.
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

### Close and unlink

- Destroying a handle releases any waiter slot it holds, then unmaps its
  view and closes its OS handles, including the events it opened.
- `unlink(name)`: Linux `shm_unlink("/sharedbox.<name>")`; Windows does
  nothing.

## Implementation language

The core is C++20, header-only, in namespace `sharedbox`. Its names are
declared in the inline namespace `sharedbox::v1`, which changes when the
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
  that can fail returns `result<T>`, a value or a `status`. On C++20
  `result<T>` is the header's own type with the part of the interface of
  `std::expected<T, status>` it needs (`has_value`, `operator bool`,
  `operator*`, `->`, `value`, `error`, `and_then`, `transform`, `or_else`,
  `value_or`); `value()` on an error calls `std::terminate`. Where the
  standard library has `std::expected` with `__cpp_lib_expected >=
  202211L` (C++23), `result<T>` is an alias for `std::expected<T,
  status>`. Every function returning it is `[[nodiscard]]`, and the header
  builds with `-fno-exceptions`.
- Every translation unit of a program must see the same `result`: build
  them all as C++20 or all as C++23.

## `sharedbox.hpp`

```cpp
namespace sharedbox {
inline namespace v1 {

inline constexpr std::uint16_t layout_major = 1, layout_minor = 0;
// Also: the layout constants handle_version, magic, header_size, name_max, max_fields,
// max_capacity, max_waiter_slots, default_waiter_slots, record_alignment, page_size,
// max_timeout, default_lock_timeout, kind_shift, capacity_mask and kind_bool, kind_int,
// kind_float, kind_str, kind_bytes; the structs header, stored_field and waiter_slot
// (see Layout); result<T> and unexpected (see Implementation language).

enum class status : int { ok = 0, exists = -1, not_found = -2, layout = -3, schema = -4,
                          corrupt = -5, lock_timeout = -6, timeout = -7, no_slot = -8,
                          range = -9, os = -10 };

struct field_spec { std::uint32_t offset; std::uint32_t capacity; std::uint8_t kind; };
struct value { std::uint16_t field; std::span<const std::byte> bytes; };
using seconds = std::chrono::duration<double>;
struct read_value { std::size_t len; std::uint64_t version; };
enum class wake { changed, interrupted };      // a timeout is status::timeout

class handle {                                  // move-only; the destructor releases it
public:
    static result<handle> create(std::string_view name, std::span<const field_spec> fields,
                                 std::uint32_t record_size, std::uint64_t schema_hash,
                                 std::uint16_t waiter_slots, std::span<const value> initial);
    static result<handle> create_unpublished(std::string_view name, std::span<const field_spec> fields,
                                             std::uint32_t record_size, std::uint64_t schema_hash,
                                             std::uint16_t waiter_slots, std::span<const value> initial);
    result<void> publish() noexcept;
    static result<handle> open(std::string_view name, seconds timeout);
    static result<handle> from_capsule(sbx_handle *capsule);
    result<handle> duplicate() const;
    sbx_handle *to_capsule() &&;

    std::string_view name() const noexcept;
    std::uint16_t field_count() const noexcept;
    const field_spec &field(std::uint16_t index) const noexcept;
    std::uint32_t record_size() const noexcept;
    std::uint64_t schema_hash() const noexcept;
    std::uint64_t create_id() const noexcept;
    std::uint16_t waiter_slots() const noexcept;
    std::uint16_t minor_version() const noexcept;
    void *base() const noexcept;
    std::uint64_t size() const noexcept;

    result<void> set_lock_timeout(seconds timeout) noexcept;
    result<read_value> read(std::uint16_t field, std::span<std::byte> buf) const;
    result<std::uint64_t> read_record(std::span<std::byte> buf) const;
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
    result<wake> wait(std::uint16_t slot, std::uint64_t last_generation, seconds timeout);
    result<void> interrupt(std::uint16_t slot);
};

result<void> unlink(std::string_view name) noexcept;
result<header> inspect(std::string_view name) noexcept;

}  // namespace v1
}  // namespace sharedbox
```

Every function returning `result` is `[[nodiscard]]`. `handle` also has
`lock`, `unlock` and `set_wait_hooks`, which the tests and the Python
extension use.

- `status`: `ok`, and one code per error the Python side raises, so the
  extension maps each to its exception class. `exists`: the name is taken.
  `not_found`: no mapping under the name, or one that never becomes a box
  within the timeout. `layout`: another `layout_major`. `schema` is kept
  for callers that compare schema hashes. `corrupt`: a header or field
  table that fails the attach checks. `lock_timeout`, `timeout`, `no_slot`:
  see Protocols. `range`: an argument out of range. `os`: an OS call
  failed, with `errno` or `GetLastError()` left as the call set it.
- `create` and `write` take raw bytes in the record encoding, without the
  length prefix of `str` and `bytes`; converting language values stays in
  each binding.
- `open` takes no schema hash; callers compare `schema_hash()` with their
  own. Its `timeout`, in `(0, 86400]`, only bounds the wait for a creator
  (the Python extension passes at most 1 s). Reads use a lock timeout of
  5 s until `set_lock_timeout` changes it; `write` takes its own.
- `read` returns `read_value`. When the stored value is longer than `buf`,
  nothing is copied and `len` says how long it is; a buffer of the field's
  capacity always fits. `read_record` copies every field from one moment
  and returns the generation of that moment; `payload` finds a field in
  such a copy.
- `inspect(name)` copies a published header without waiting and without
  the checks of `open`. The Python extension uses it to name the layout of
  a segment it refuses and the creator in `SegmentExistsError`.
- `duplicate()` makes a second handle with its own mapping from this
  handle's OS handle (`dup` and `mmap` on Linux, `DuplicateHandle` and
  `MapViewOfFile` on Windows), not from the name, so it works after
  `unlink()`.
- `minor_version()` is the lower of the segment's `layout_minor` and the
  `layout_minor` of the `sharedbox.hpp` the caller was compiled with.
- Shared memory that never becomes a box within the timeout is
  `status::not_found`.
- Thread safety: every member function may be called from several threads
  on one handle. Destroying or moving a handle must not overlap another
  call on it.
- Known limit: on Linux every open handle keeps the mapping's file
  descriptor, so about 1000 open boxes reach the default `ulimit -n` of
  1024.

### Capsule handle

`sbx_handle` is the struct a capsule holds. It is plain C data with function
pointers, declared in `sharedbox_c.h` and included by `sharedbox.hpp`,
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
  set or `size` is below 4096, then runs steps 3 to 5 of Attach on the
  segment at `base`. It keeps its own copy of the struct and of the field table, and
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

- It holds only `sbx_handle`, `sbx_value`, the status codes and these six
  functions. Creating, waiting, `interrupt`, `force_unlock` and `unlink`
  are in the C++ API only.
- Each function converts its arguments and calls `sharedbox.hpp`.
  `sbx_open` fills `out` with a handle whose `private_data` holds a
  `sharedbox::handle` and whose `release` deletes it. `sbx_import` wraps a
  capsule's handle through `handle::from_capsule`.
- `sbx_read`, `sbx_write` and `sbx_schema_hash` accept only handles made
  by `sbx_open` or `sbx_import`; for any other, `sbx_read` and `sbx_write`
  return `SBX_E_RANGE` and `sbx_schema_hash` returns 0. When the stored
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
    camera.run(frame)          # an extension that supports sharedbox
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
- `sharedbox._native.LAYOUT_VERSION` is `(1, 0)`.

## Rust crate (deferred)

A Rust crate is not part of this release. It will be a safe layer over
`sharedbox.hpp` through cxx, so Python, C++ and Rust share one
implementation of the protocols, with `schema_hash` and the default name
computed in Rust and checked against the test vector above.

## Build and packaging

- `include/sharedbox/sharedbox.hpp`, `sharedbox_c.h` and `sharedbox_c.cpp`
  are in the repository; the build installs them into the wheel under
  `sharedbox/include/`, and `cmake/sharedbox-config.cmake` under
  `sharedbox/share/cmake/sharedbox/`, next to a
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

- Queues and streams; they would get `__sharedbox_queue__` and
  `__sharedbox_stream__`.
- `Global\` names on Windows. They would let a service in session 0 share
  a box with a desktop application, but creating a file mapping there
  needs `SeCreateGlobalPrivilege` and a security descriptor that lets the
  other session's user open it.
- A vcpkg port and a headers-only wheel.
- macOS. Every platform detail lives in `sharedbox.hpp`, so macOS would be
  a third platform there. Known obstacles: POSIX shared memory names are
  limited to 31 characters, which `sharedbox.<name>` with 128-character
  box names does not fit; there is no futex; there is no
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
