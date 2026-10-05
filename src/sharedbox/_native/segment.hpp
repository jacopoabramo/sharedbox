#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <sharedbox/sharedbox.hpp>

namespace sharedbox {

enum class FieldKind : std::uint8_t { Bool = 0, Int = 1, Float = 2, Str = 3, Bytes = 4, Ref = 5 };

struct FieldDesc {
    std::uint32_t offset;
    std::uint32_t capacity;
    FieldKind kind;
};

/// Throws std::out_of_range unless index < count.
void check_index(std::uint32_t index, std::size_t count);
/// Throws std::invalid_argument unless there is one name per field.
void check_names(const std::vector<std::string> &names, std::size_t count);
/// True for str and bytes, whose payload follows a 4-byte length.
bool is_prefixed(FieldKind kind);
std::size_t field_alignment(FieldKind kind);
/// Bytes the field occupies in the record, the length prefix included.
std::size_t field_span(const FieldDesc &field);
/// The stored bytes of field inside a copy of the record made by Segment::read_record.
std::string_view payload(const FieldDesc &field, const std::byte *record);

struct SegmentExists : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct SegmentMissing : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct SchemaMismatch : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct SegmentClosed : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct LockTimeout : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct NoWaiterSlot : std::runtime_error {
    using std::runtime_error::runtime_error;
};

using WaitHook = void *(*)();
using ResumeHook = void (*)(void *);
/// Called around a wait for another writer's lock; module.cpp releases the GIL there.
void set_wait_hooks(WaitHook before, ResumeHook after);

/// One field as Segment::read found it. bytes points into this object, so it cannot be copied.
struct FieldRead {
    FieldRead() = default;
    FieldRead(const FieldRead &) = delete;
    FieldRead &operator=(const FieldRead &) = delete;

    const FieldDesc *field = nullptr;
    std::uint64_t version = 0;
    std::string_view bytes;
    // Most values fit in words. Words rather than chars, so MSVC's /GS adds no stack cookie check to
    // every read. A value longer than words is read again into large, sized from its stored length or,
    // for a described kind, its size; new[] leaves it uninitialised.
    std::uint64_t words[32];
    std::unique_ptr<std::byte[]> large;
};

/// A named shared-memory segment holding one fixed-layout record.
class Segment {
public:
    /// values are written before any other process can attach; names label fields in error messages.
    /// Without publish, no other process can attach until publish() is called.
    static std::unique_ptr<Segment>
    create(const std::string &name, const std::vector<FieldDesc> &fields, const std::vector<std::string> &names,
           std::uint64_t record_size, std::uint64_t schema_hash, double lock_timeout, std::uint16_t waiter_slots,
           const std::vector<std::pair<std::uint32_t, std::string>> &values, const std::string &types_table = {},
           std::uint16_t major = layout_major, bool publish = true);
    /// With types_table, the segment's description table must equal it.
    static std::unique_ptr<Segment> attach(const std::string &name, const std::vector<std::string> &names,
                                           std::uint64_t schema_hash, double lock_timeout,
                                           const std::string *types_table);
    ~Segment();
    Segment(const Segment &) = delete;
    Segment &operator=(const Segment &) = delete;

    /// Reads the field's bytes and version, from one moment, into out.
    void read(std::uint32_t field, FieldRead &out) const;
    /// Bytes of the record, the size of the buffer read_record fills.
    std::size_t record_size() const;
    /// Copies the whole record, every field from one moment, into out, which holds record_size() bytes;
    /// payload() finds a field in it.
    void read_record(std::span<std::byte> out) const;
    std::uint32_t field_count() const;
    /// Writes values under one lock; each field index was checked against field_count() before it
    /// was narrowed into a value.
    void write(std::span<const value> values);
    /// write() of a single value.
    void write_one(std::uint32_t field, std::span<const std::byte> bytes);
    std::uint64_t version(std::uint32_t field) const;
    /// version() of every field, in field order.
    std::vector<std::uint64_t> versions() const;
    /// Lets other processes attach to a segment created without publish; throws std::invalid_argument
    /// if it is already published.
    void publish();
    /// False from create without publish until publish().
    bool published() const;
    std::uint64_t generation() const;
    /// Claims a waiter slot for this process; free it with release_waiter.
    std::uint16_t register_waiter();
    void release_waiter(std::uint16_t slot);
    /// Ends the wait in slot, of any process, or the next one if none is running.
    void interrupt(std::uint16_t slot);
    /// Whether slot is still this process's; false once it was freed under it.
    bool waiter_held(std::uint16_t slot) const;
    /// Occupied waiter slots; for tests.
    std::uint32_t waiters() const;
    /// Random at creation; a box made again under the same name has another.
    std::uint64_t create_id() const;
    /// A heap handle with its own mapping of the segment, for a capsule. Call its release, then
    /// delete it.
    sbx_handle *export_handle() const;
    /// Returns the generation once it differs from last_generation, the slot is interrupted, or
    /// timeout seconds pass. Without a slot the call claims one for its own duration.
    std::uint64_t wait(std::uint64_t last_generation, double timeout, std::optional<std::uint16_t> slot) const;
    void force_unlock();
    /// Takes the write lock and keeps it until release_held_lock; exists for tests.
    void hold_write_lock();
    void release_held_lock();
    /// Resets per-process locks in a child created by fork(); call before any other thread starts.
    void after_fork();
    /// Detaches this handle; the segment itself stays until unlinked.
    void close();
    /// Bytes of the segment's mapping.
    std::uint64_t size() const;
    std::uint16_t major_version() const;
    std::uint16_t minor_version() const;
    bool closed() const;
    const std::string &name() const;
    double lock_timeout() const;
    /// Every field, in index order; fixed once the segment is open.
    std::span<const FieldDesc> fields() const;
    /// The label of every field, in index order; fixed once the segment is open.
    std::span<const std::string> field_names() const;
    /// Removes the name, like shm_unlink: existing handles keep working. A no-op on
    /// Windows, where the OS frees the segment when its last handle closes.
    static void unlink(const std::string &name);

private:
    struct Impl;
    explicit Segment(std::unique_ptr<Impl> impl);
    std::unique_ptr<Impl> impl_;
};

} // namespace sharedbox
