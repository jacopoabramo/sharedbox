// box.hpp: layout 3.0 of a box segment and the protocols that use it.
#ifndef SHAREDBOX_BOX_HPP
#define SHAREDBOX_BOX_HPP

#include <sharedbox/core.hpp>

namespace sharedbox {
inline namespace v3 {

inline constexpr std::uint16_t layout_major = 3;
inline constexpr std::uint16_t layout_minor = 0;
inline constexpr std::uint32_t handle_version = 1;
inline constexpr std::uint32_t header_size = 128;
inline constexpr std::uint32_t record_alignment = 64;
inline constexpr double default_lock_timeout = 5.0;
// A box keeps its page-rounded mapping below 4 GiB, so every offset in its header is 32 bits.
inline constexpr std::uint64_t max_mapping_size = (std::uint64_t{1} << 32) - page_size;
// A field as create takes it and field() returns it; kind is one of the kind_ constants.
struct field_spec {
    std::uint32_t offset;
    std::uint32_t capacity;
    std::uint8_t kind;
};

// Bytes for one field in the record encoding, without the length prefix of str, bytes and decimal.
struct value {
    std::uint16_t field;
    std::span<const std::byte> bytes;
};

// len is the field's stored length; when it exceeds the buffer, nothing was copied.
struct read_value {
    std::size_t len;
    std::uint64_t version;
};

// An array field held for writing in place: its bytes in the record, and the even seq begin_write locked
// from, which end_write releases.
struct field_write {
    std::uint16_t field;
    std::span<std::byte> bytes;
    std::uint64_t locked;
};

// A box's header: the common line 0, then line 1 with the words writes and waits change and the
// geometry, written once, which only opening reads.
struct header {
    common_header common;
    std::uint64_t seq;
    std::uint32_t writer_pid;
    std::uint32_t wake_word;
    std::uint32_t waiters;
    std::uint16_t field_count;
    std::uint16_t reserved1;
    std::uint32_t record_size;
    std::uint32_t record;
    std::uint32_t tail;
    // Bytes of the description table after the waiter slots.
    std::uint32_t types_size;
    std::uint8_t reserved2[24];
};

struct stored_field {
    std::uint32_t offset;
    std::uint32_t capacity_and_kind;
};

static_assert(std::is_standard_layout_v<header> && std::is_trivially_copyable_v<header>);
static_assert(std::is_standard_layout_v<stored_field> && std::is_trivially_copyable_v<stored_field>);
static_assert(std::is_standard_layout_v<sbx_handle> && std::is_trivially_copyable_v<sbx_handle>);
static_assert(sizeof(header) == 128 && alignof(header) == 8);
static_assert(offsetof(header, common) == 0);
static_assert(offsetof(header, seq) == 64 && offsetof(header, writer_pid) == 72 &&
              offsetof(header, wake_word) == 76 && offsetof(header, waiters) == 80);
static_assert(offsetof(header, field_count) == 84 && offsetof(header, reserved1) == 86 &&
              offsetof(header, record_size) == 88 && offsetof(header, record) == 92 &&
              offsetof(header, tail) == 96 && offsetof(header, types_size) == 100 &&
              offsetof(header, reserved2) == 104);
static_assert(sizeof(stored_field) == 8 && alignof(stored_field) == 4);
static_assert(offsetof(stored_field, offset) == 0);
static_assert(offsetof(stored_field, capacity_and_kind) == 4);
static_assert(sizeof(sbx_handle) == 48 && alignof(sbx_handle) == 8);
static_assert(offsetof(sbx_handle, layout_major) == 0);
static_assert(offsetof(sbx_handle, layout_minor) == 2);
static_assert(offsetof(sbx_handle, handle_version) == 4);
static_assert(offsetof(sbx_handle, base) == 8);
static_assert(offsetof(sbx_handle, size) == 16);
static_assert(offsetof(sbx_handle, name) == 24);
static_assert(offsetof(sbx_handle, release) == 32);
static_assert(offsetof(sbx_handle, private_data) == 40);

static_assert(offsetof(header, seq) % detail::align64 == 0);
static_assert(offsetof(header, writer_pid) % detail::align32 == 0 &&
              offsetof(header, wake_word) % detail::align32 == 0 &&
              offsetof(header, waiters) % detail::align32 == 0);
// The write counts start at header_size + field_count * sizeof(stored_field), the slots after them.
static_assert(header_size % detail::align64 == 0 && sizeof(stored_field) % detail::align64 == 0);
static_assert(sizeof(waiter_slot) % detail::align64 == 0 && page_size % alignof(header) == 0);
namespace detail {

// Bytes the field occupies in the record, the length prefix of str, bytes and decimal included.
inline std::uint64_t field_span(const field_spec &f) noexcept {
    return (prefixed(f.kind) ? 4u : 0u) + std::uint64_t{f.capacity};
}

// Whether any two fields share a byte of the record; at most max_fields, so pairs are compared.
inline bool fields_overlap(std::span<const field_spec> fields) noexcept {
    for (std::size_t i = 0; i < fields.size(); ++i)
        for (std::size_t j = i + 1; j < fields.size(); ++j)
            if (fields[i].offset < fields[j].offset + field_span(fields[j]) &&
                fields[j].offset < fields[i].offset + field_span(fields[i]))
                return true;
    return false;
}
// Where the waiter slots end and the padding before the record starts.
inline std::uint64_t tail_end(std::uint32_t field_count, std::uint32_t waiter_slots) noexcept {
    return header_size + std::uint64_t{field_count} * (sizeof(stored_field) + sizeof(std::uint64_t)) +
           std::uint64_t{waiter_slots} * sizeof(waiter_slot);
}

// Sets each field's kind and capacity from its type, then checks the fields lie inside the record,
// aligned, without sharing a byte. create refuses a kind this header does not know; open keeps it opaque.
inline status place_fields(field_spec *fields, std::uint16_t count, const type_table &types,
                           std::uint32_t record_size, bool unknown_ok) noexcept {
    for (std::uint16_t i = 0; i < count; ++i) {
        const type_ref &t = types.field(i);
        field_spec &f = fields[i];
        if (!unknown_ok && !kind_known(t.kind))
            return status::corrupt;
        f.kind = t.kind;
        f.capacity = prefixed(t.kind) ? t.size - 4 : t.size;
        if (f.offset % t.alignment != 0 || f.offset > record_size || t.size > record_size - f.offset)
            return status::corrupt;
    }
    return fields_overlap({fields, count}) ? status::corrupt : status::ok;
}
struct box_layout {
    static constexpr std::uint64_t magic = box_magic;
    static constexpr std::uint16_t oldest_major = layout_major;
    static constexpr std::uint16_t newest_major = layout_major;
    using header_type = header;
    // Line 1 of the copied header, after its line 0 passed the core checks.
    static status check_geometry(const header &h, std::uint64_t mapped) noexcept {
        if (mapped > max_mapping_size)
            return status::corrupt;
        if (h.field_count == 0 || h.field_count > max_fields)
            return status::corrupt;
        if (h.tail != header_size || h.record % record_alignment != 0)
            return status::corrupt;
        const std::uint64_t slots_end = tail_end(h.field_count, h.common.waiter_slots);
        const std::uint64_t record_end = std::uint64_t{h.record} + h.record_size;
        if (h.types_size % 8 != 0 || h.types_size > max_types_size ||
            h.record < round_up(slots_end + h.types_size, record_alignment) || record_end > max_mapping_size)
            return status::corrupt;
        return record_end > mapped ? status::corrupt : status::ok;
    }
};
static_assert(segment_layout<box_layout>);

// The header at base without seq, writer_pid, wake_word and waiters, which other processes change while
// they are open: line 0 and the bytes of line 1 written once. Only the copy is checked and used.
inline header copy_header(const void *base) noexcept {
    header h{};
    std::memcpy(&h, base, offsetof(header, seq));
    constexpr std::size_t once = offsetof(header, field_count);
    std::memcpy(reinterpret_cast<std::byte *>(&h) + once, static_cast<const std::byte *>(base) + once,
                sizeof h - once);
    return h;
}

// Copies the header of a published segment of mapped bytes and checks the copy as a box.
inline result<header> open_header(const void *base, std::uint64_t mapped) noexcept {
    if (mapped < page_size)
        return unexpected(status::corrupt);
    const header h = copy_header(base);
    if (const result<common_header> line0 = open_check<box_layout>(h, mapped); !line0)
        return unexpected(line0.error());
    return h;
}

struct state;

} // namespace detail

class handle;

namespace detail {

[[nodiscard]] inline result<handle> create_impl(std::string_view name, std::span<const field_spec> fields,
                                                std::uint32_t record_size, std::uint64_t schema_hash,
                                                std::uint16_t waiter_slots, std::span<const value> initial,
                                                std::span<const std::byte> types, bool publish);

} // namespace detail

// A box's segment, mapped into this process. Move-only; the destructor releases it. Every member
// function may be called from several threads at once; destroying or moving a handle must not overlap
// another call on it.
class handle {
public:
    handle() noexcept = default;
    handle(handle &&other) noexcept : s_(std::exchange(other.s_, nullptr)) {}
    handle &operator=(handle &&other) noexcept {
        handle(std::move(other)).swap(*this);
        return *this;
    }
    ~handle();

    // Creates the box: the name, the mapping, the header and field table, every waiter slot free, and
    // the initial values; no other process can open it before all of that is written. types is the
    // description table of the fields of a described kind; empty when there are none.
    [[nodiscard]] static result<handle> create(std::string_view name, std::span<const field_spec> fields,
                                               std::uint32_t record_size, std::uint64_t schema_hash,
                                               std::uint16_t waiter_slots, std::span<const value> initial,
                                               std::span<const std::byte> types = {});
    // As create, but open waits until publish() is called, so the creator can change the values first.
    [[nodiscard]] static result<handle>
    create_unpublished(std::string_view name, std::span<const field_spec> fields, std::uint32_t record_size,
                       std::uint64_t schema_hash, std::uint16_t waiter_slots, std::span<const value> initial,
                       std::span<const std::byte> types = {});
    // Lets open find a box made by create_unpublished. status::range if the box is already published,
    // as every box a handle made by open or from_capsule reaches is. A handle from duplicate() of an
    // unpublished creator shares its mapping and could publish it, so a creator must not hand one out
    // before publishing.
    [[nodiscard]] result<void> publish() noexcept;
    // Opens the box called name, waiting up to timeout for a creator that has not finished. The caller
    // compares schema_hash() with its own.
    [[nodiscard]] static result<handle> open(std::string_view name, seconds timeout);

    std::string_view name() const noexcept;
    std::uint16_t field_count() const noexcept;
    // The field as checked when the handle was made; later changes to the shared table are ignored.
    // index must be below field_count().
    const field_spec &field(std::uint16_t index) const noexcept;
    // The type of a field: kind, size and, for a described kind, its description. Valid while this handle
    // lives; index must be below field_count().
    type_view field_type(std::uint16_t index) const noexcept;
    // The description table as checked when the handle was made; empty for a 1.0 segment.
    std::span<const std::byte> types_table() const noexcept;
    std::uint32_t record_size() const noexcept;
    std::uint64_t schema_hash() const noexcept;
    // Random at creation, so a box made again under the same name has another.
    std::uint64_t create_id() const noexcept;
    std::uint16_t waiter_slots() const noexcept;
    // The segment's minor version, or this header's if that is lower.
    std::uint16_t minor_version() const noexcept;
    // The segment's major version: 1 for a box made by a 0.3 release, 2 otherwise.
    std::uint16_t major_version() const noexcept;
    void *base() const noexcept;
    std::uint64_t size() const noexcept;

    // How long a read waits for another writer's lock before it gives status::lock_timeout; 5 s until set.
    [[nodiscard]] result<void> set_lock_timeout(seconds timeout) noexcept;
    // Called around a wait for another writer's lock: before the first pause and after the last.
    void set_wait_hooks(void *(*before)(), void (*after)(void *)) noexcept;
    // Copies the field's stored bytes, and no more, into buf, with the field's write count from the same
    // moment. If they do not fit, nothing is copied and len says how many bytes they need; a buf of the
    // field's capacity always fits.
    [[nodiscard]] result<read_value> read(std::uint16_t field, std::span<std::byte> buf) const;
    // Copies the whole record, every field from one moment, into buf of at least record_size() bytes,
    // and returns the generation of that moment.
    [[nodiscard]] result<std::uint64_t> read_record(std::span<std::byte> buf) const;
    // As read, for a list, set or dict field: copies the length and the slots the value uses, not the
    // whole capacity. status::range for a field of another kind.
    [[nodiscard]] result<read_value> read_used(std::uint16_t field, std::span<std::byte> buf) const;
    // As read and read_record, for values of large_copy bytes or more: the wait hooks run once around
    // the whole call, and a copy starts again as soon as a writer moves the sequence number.
    [[nodiscard]] result<read_value> read_large(std::uint16_t field, std::span<std::byte> buf) const;
    [[nodiscard]] result<std::uint64_t> read_record_large(std::span<std::byte> buf) const;
    // As write, with the wait hooks run once around the whole call.
    [[nodiscard]] result<void> write_large(std::span<const value> values, seconds lock_timeout);
    // The stored bytes of field inside a copy made by read_record.
    std::span<const std::byte> payload(std::uint16_t field, std::span<const std::byte> record) const noexcept;
    // Writes every value under one lock, so readers see all of them or none, then wakes waiters.
    // status::range for a field of a kind this header does not know.
    [[nodiscard]] result<void> write(std::span<const value> values, seconds lock_timeout);
    // Writes since creation, seq >> 1: every write adds 2 to seq, and a force_unlock counts as one.
    std::uint64_t generation() const noexcept;
    // Writes to the field since creation; field must be below field_count().
    std::uint64_t version(std::uint16_t field) const noexcept;
    // The pid holding the write lock, 0 when free; after force_unlock, the last holder.
    std::uint32_t writer_pid() const noexcept;
    // Releases a write lock left by a process that died while writing. writer_pid keeps its value.
    [[nodiscard]] result<void> force_unlock() noexcept;
    // Takes the write lock and returns the even seq it was taken from, for unlock; write does both
    // itself. For tests that hold the lock.
    [[nodiscard]] result<std::uint64_t> lock(seconds lock_timeout);
    void unlock(std::uint64_t locked) noexcept;
    // Takes the write lock and returns the bytes of an array field for writing in place, with the seq to end
    // with. status::range for a field past field_count() or of another kind. Readers in every process retry
    // until end_write.
    [[nodiscard]] result<field_write> begin_write(std::uint16_t field, seconds lock_timeout);
    // Adds one to the field's write count, releases the lock begin_write took and wakes waiters.
    void end_write(const field_write &w) noexcept;

    // Claims a free waiter slot for this process, first freeing the slots of processes that have exited.
    [[nodiscard]] result<std::uint16_t> register_waiter();
    // Frees a slot this handle claimed; any other slot is left alone.
    void release_waiter(std::uint16_t slot) noexcept;
    // Whether this handle claimed slot and the slot still records this process; false once it was freed
    // under it, when the caller releases it and claims another.
    bool waiter_held(std::uint16_t slot) const noexcept;
    // Occupied waiter slots.
    std::uint32_t waiters() const noexcept;
    // Blocks in a slot this handle holds until the generation differs from last_generation, the slot is
    // interrupted, or timeout passes (status::timeout).
    [[nodiscard]] result<wake> wait(std::uint16_t slot, std::uint64_t last_generation, seconds timeout);
    // Ends the wait in slot, of any process, with wake::interrupted; the flag stays set until that waiter
    // sees it, so an interrupt sent before the wait starts still ends it. An interrupt that arrives after the
    // slot was released and claimed again reaches the next owner as one spurious wake::interrupted.
    [[nodiscard]] result<void> interrupt(std::uint16_t slot);

    // Takes over a handle made by another build, such as the one in a "sharedbox_box" capsule: checks
    // the segment at capsule->base as open does and, on success, clears capsule->release; destroying
    // the new handle calls the producer's release.
    [[nodiscard]] static result<handle> from_capsule(sbx_handle *capsule);
    // A second handle on this segment with its own mapping, made from this handle's OS handle rather
    // than the name, so it works after unlink. status::range for a handle made by from_capsule.
    [[nodiscard]] result<handle> duplicate() const;
    // A heap sbx_handle that owns this handle; its release destroys the handle, and the caller deletes
    // the struct afterwards. nullptr when memory runs out, with this handle left as it was.
    [[nodiscard]] sbx_handle *to_capsule() &&;

private:
    template <bool Hooks> result<std::uint64_t> lock_with(seconds lock_timeout);
    explicit handle(detail::state *s) noexcept : s_(s) {}
    void swap(handle &other) noexcept { std::swap(s_, other.s_); }

    detail::state *s_ = nullptr;

    friend result<handle> detail::create_impl(std::string_view name, std::span<const field_spec> fields,
                                              std::uint32_t record_size, std::uint64_t schema_hash,
                                              std::uint16_t waiter_slots, std::span<const value> initial,
                                              std::span<const std::byte> types, bool publish);
};

namespace detail {

// What a handle keeps: the checked copy of line 0 and the field table, pointers into the mapping, and
// the per-process bookkeeping.
struct state {
    char name[name_max + 1] = {};
    std::uint16_t field_count = 0;
    std::uint16_t waiter_slots = 0;
    std::uint16_t layout_major = 0;
    std::uint16_t layout_minor = 0;
    std::uint32_t record_size = 0;
    std::uint32_t record_offset = 0;
    std::uint64_t schema_hash = 0;
    std::uint64_t create_id = 0;
    header *hdr = nullptr;
    std::uint64_t *counts = nullptr;
    waiter_slot *slots = nullptr;
    std::byte *record = nullptr;
    std::uint64_t size = 0;
    std::unique_ptr<field_spec[]> fields;
    type_table types;
    os_mapping map;
    double lock_timeout = default_lock_timeout;
    void *(*before_wait)() = nullptr;
    void (*after_wait)(void *) = nullptr;
    std::array<std::atomic<std::uint64_t>, max_waiter_slots / 64> owned{};
#ifdef _WIN32
    std::unique_ptr<std::atomic<HANDLE>[]> events;
#endif
    sbx_handle foreign{};

    ~state();
};

inline bool owned_clear(state &s, std::uint16_t i) noexcept;
inline bool slot_is_mine(const state &s, std::uint16_t i) noexcept;
inline void free_slot(const state &s, std::uint16_t i) noexcept;
inline void free_dead_waiters(const state &s) noexcept;
SHAREDBOX_HOT void wake_waiters(const state &s) noexcept;
#ifdef _WIN32
inline HANDLE event(const state &s, std::uint16_t slot) noexcept;
#endif

inline state::~state() {
    if (hdr != nullptr)
        for (std::uint16_t i = 0; i < waiter_slots; ++i)
            // A child created by fork inherits the bits of its parent's slots; those stay the parent's.
            if (owned_clear(*this, i) && slot_is_mine(*this, i))
                free_slot(*this, i);
#ifdef _WIN32
    if (events != nullptr)
        for (std::uint16_t i = 0; i < waiter_slots; ++i)
            if (HANDLE e = events[i].load(std::memory_order_relaxed); e != nullptr)
                CloseHandle(e);
#endif
    if (foreign.release != nullptr)
        foreign.release(&foreign);
}

// A state for a mapping of the given shape, or nullptr when memory runs out.
inline std::unique_ptr<state> make_state(std::string_view name, std::uint16_t field_count,
                                         std::uint16_t waiter_slots) noexcept {
    std::unique_ptr<state> s(new (std::nothrow) state);
    if (s == nullptr)
        return nullptr;
    s->fields.reset(new (std::nothrow) field_spec[field_count]);
    if (s->fields == nullptr)
        return nullptr;
#ifdef _WIN32
    s->events.reset(new (std::nothrow) std::atomic<HANDLE>[waiter_slots]());
    if (s->events == nullptr)
        return nullptr;
#endif
    std::memcpy(s->name, name.data(), name.size());
    s->field_count = field_count;
    s->waiter_slots = waiter_slots;
    return s;
}

// Points s into the mapping at base, whose line 0 and field table s already holds.
inline void bind(state &s, void *base, std::uint64_t size, std::uint16_t segment_major,
                 std::uint16_t segment_minor) noexcept {
    auto *bytes = static_cast<std::byte *>(base);
    const std::size_t table = header_size + std::size_t{s.field_count} * sizeof(stored_field);
    s.hdr = static_cast<header *>(base);
    s.counts = reinterpret_cast<std::uint64_t *>(bytes + table);
    s.slots = reinterpret_cast<waiter_slot *>(bytes + table + std::size_t{s.field_count} * sizeof(std::uint64_t));
    s.record = bytes + s.record_offset;
    s.size = size;
    s.layout_major = segment_major;
    s.layout_minor = segment_minor < sharedbox::layout_minor ? segment_minor : sharedbox::layout_minor;
}

// Copies and checks the field table and the description table of a mapping; h is the copy of its
// header, which passed open_check.
inline status copy_fields(state &s, const void *base, const header &h) noexcept {
    const auto *bytes = static_cast<const std::byte *>(base);
    std::unique_ptr<std::uint32_t[]> entries(new (std::nothrow) std::uint32_t[s.field_count]);
    if (entries == nullptr)
        return status::os;
    for (std::uint16_t i = 0; i < s.field_count; ++i) {
        stored_field stored;
        std::memcpy(&stored, bytes + header_size + std::size_t{i} * sizeof stored, sizeof stored);
        s.fields[i].offset = stored.offset;
        entries[i] = stored.capacity_and_kind;
    }
    const std::span<const std::byte> table(bytes + tail_end(s.field_count, s.waiter_slots), h.types_size);
    if (const status rc = s.types.parse(table, {entries.get(), s.field_count}); rc != status::ok)
        return rc;
    return place_fields(s.fields.get(), s.field_count, s.types, s.record_size, true);
}

inline bool is_collection(std::uint32_t kind) noexcept { return kind - kind_list <= 2u; }

// A list, set or dict value is its length and the slots it uses, so its bytes may be shorter than the field.
inline bool collection_fits(const type_node &node, std::span<const std::byte> bytes) noexcept {
    if (bytes.size() < node.slots)
        return false;
    std::uint32_t length = 0;
    std::memcpy(&length, bytes.data(), sizeof length);
    return length <= node.capacity && bytes.size() == node.slots + std::uint64_t{length} * node.stride;
}

SHAREDBOX_HOT bool values_ok(const state &s, std::span<const value> values) noexcept {
    for (const value &v : values) {
        if (v.field >= s.field_count)
            return false;
        const field_spec &f = s.fields[v.field];
        if (!kind_known(f.kind))
            return false;
        if (prefixed(f.kind)) {
            if (v.bytes.size() > f.capacity)
                return false;
        } else if (is_collection(f.kind)) [[unlikely]] {
            if (!collection_fits(s.types.node(s.types.field(v.field).node), v.bytes))
                return false;
        } else if (v.bytes.size() != f.capacity) {
            return false;
        }
    }
    return true;
}

SHAREDBOX_HOT void store(const state &s, const value &v) noexcept {
    const field_spec &f = s.fields[v.field];
    std::byte *dst = s.record + f.offset;
    if (prefixed(f.kind)) {
        const auto length = static_cast<std::uint32_t>(v.bytes.size());
        std::memcpy(dst, &length, sizeof length);
        dst += sizeof length;
    }
    if (!v.bytes.empty())
        std::memcpy(dst, v.bytes.data(), v.bytes.size());
}

// The field's payload in record; a torn read may see any length, so it is capped at the capacity.
SHAREDBOX_HOT std::span<const std::byte> payload(const field_spec &f, const std::byte *record) noexcept {
    const std::byte *src = record + f.offset;
    if (!prefixed(f.kind))
        return {src, f.capacity};
    std::uint32_t length;
    std::memcpy(&length, src, sizeof length);
    return {src + sizeof length, length > f.capacity ? f.capacity : length};
}

// create and create_unpublished.
inline result<handle> create_impl(std::string_view name, std::span<const field_spec> fields,
                                  std::uint32_t record_size, std::uint64_t schema_hash, std::uint16_t waiter_slots,
                                  std::span<const value> initial, std::span<const std::byte> types, bool publish) {
    if (!name_ok(name) || fields.empty() || fields.size() > max_fields || waiter_slots == 0 ||
        waiter_slots > max_waiter_slots)
        return unexpected(status::range);
    if (std::uint64_t{record_size} > max_mapping_size)
        return unexpected(status::range);
    const auto count = static_cast<std::uint16_t>(fields.size());
    std::unique_ptr<std::uint32_t[]> entries(new (std::nothrow) std::uint32_t[fields.size()]);
    if (entries == nullptr)
        return unexpected(status::os);
    for (std::size_t i = 0; i < fields.size(); ++i) {
        if (fields[i].capacity > capacity_mask)
            return unexpected(status::range);
        entries[i] = fields[i].capacity | std::uint32_t{fields[i].kind} << kind_shift;
    }
    std::unique_ptr<state> s = make_state(name, count, waiter_slots);
    if (s == nullptr)
        return unexpected(status::os);
    std::copy(fields.begin(), fields.end(), s->fields.get());
    // A table the caller built wrong is its mistake, status::range; running out of memory is not.
    if (const status rc = s->types.parse(types, {entries.get(), count}); rc != status::ok)
        return unexpected(rc == status::os ? status::os : status::range);
    if (place_fields(s->fields.get(), count, s->types, record_size, false) != status::ok ||
        !values_ok(*s, initial))
        return unexpected(status::range);
    const auto record =
        static_cast<std::uint32_t>(round_up(tail_end(count, waiter_slots) + types.size(), record_alignment));
    if (std::uint64_t{record} + record_size > max_mapping_size)
        return unexpected(status::range);
    const std::uint64_t size = round_up(std::uint64_t{record} + record_size, page_size);
    const result<std::uint64_t> id = random_id();
    if (!id)
        return unexpected(id.error());
    result<os_mapping> map = map_create(name, size);
    if (!map)
        return unexpected(map.error());
    s->map = std::move(*map);
    auto *base = s->map.base();
    header &h = *static_cast<header *>(base);
    h.common.core_major = core_major;
    h.common.core_minor = core_minor;
    h.common.kind_major = layout_major;
    h.common.kind_minor = layout_minor;
    h.common.waiter_slots = waiter_slots;
    h.common.schema_hash = schema_hash;
    h.common.size = size;
    h.common.create_id = *id;
    h.common.creator_start = current_start();
    h.common.creator_pid = current_pid();
    h.common.creator_pidns = current_pidns();
    h.field_count = count;
    h.record_size = record_size;
    h.record = record;
    h.tail = header_size;
    h.types_size = static_cast<std::uint32_t>(types.size());
    for (std::uint16_t i = 0; i < count; ++i) {
        const stored_field stored{fields[i].offset, entries[i]};
        std::memcpy(static_cast<std::byte *>(base) + header_size + std::size_t{i} * sizeof stored, &stored,
                    sizeof stored);
    }
    if (!types.empty())
        std::memcpy(static_cast<std::byte *>(base) + tail_end(count, waiter_slots), types.data(), types.size());
    s->record_size = record_size;
    s->record_offset = record;
    s->schema_hash = schema_hash;
    s->create_id = *id;
    bind(*s, base, size, layout_major, layout_minor);
    for (const value &v : initial)
        store(*s, v);
    handle made(s.release());
    if (publish)
        static_cast<void>(made.publish());
    return made;
}

} // namespace detail

inline handle::~handle() { delete s_; }

inline result<handle> handle::create(std::string_view name, std::span<const field_spec> fields,
                                     std::uint32_t record_size, std::uint64_t schema_hash,
                                     std::uint16_t waiter_slots, std::span<const value> initial,
                                     std::span<const std::byte> types) {
    return detail::create_impl(name, fields, record_size, schema_hash, waiter_slots, initial, types, true);
}

inline result<handle> handle::create_unpublished(std::string_view name, std::span<const field_spec> fields,
                                                 std::uint32_t record_size, std::uint64_t schema_hash,
                                                 std::uint16_t waiter_slots, std::span<const value> initial,
                                                 std::span<const std::byte> types) {
    return detail::create_impl(name, fields, record_size, schema_hash, waiter_slots, initial, types, false);
}

inline result<void> handle::publish() noexcept {
    std::uint64_t unpublished = 0;
    if (!detail::atomic(s_->hdr->common.magic)
             .compare_exchange_strong(unpublished, box_magic, std::memory_order_release,
                                      std::memory_order_relaxed))
        return unexpected(status::range);
    return {};
}

inline result<handle> handle::open(std::string_view name, seconds timeout) {
    if (!detail::name_ok(name) || !detail::timeout_ok(timeout, false))
        return unexpected(status::range);
    detail::backoff wait(timeout.count());
    result<detail::os_mapping> map = detail::map_open(name, wait);
    if (!map)
        return unexpected(map.error());
    auto *hdr = static_cast<header *>(map->base());
    while (detail::atomic(hdr->common.magic).load(std::memory_order_acquire) == 0) {
        if (wait.expired())
            return unexpected(status::not_found);
        wait.pause();
    }
    const result<header> opened = detail::open_header(hdr, map->size());
    if (!opened)
        return unexpected(opened.error());
    const header &h = *opened;
    std::unique_ptr<detail::state> s = detail::make_state(name, h.field_count, h.common.waiter_slots);
    if (s == nullptr)
        return unexpected(status::os);
    s->record_size = h.record_size;
    if (const status rc = detail::copy_fields(*s, hdr, h); rc != status::ok)
        return unexpected(rc);
    s->record_offset = h.record;
    s->schema_hash = h.common.schema_hash;
    s->create_id = h.common.create_id;
    s->map = std::move(*map);
    detail::bind(*s, s->map.base(), s->map.size(), h.common.kind_major, h.common.kind_minor);
    detail::free_dead_waiters(*s);
    return handle(s.release());
}

inline std::string_view handle::name() const noexcept { return s_->name; }
inline std::uint16_t handle::field_count() const noexcept { return s_->field_count; }
inline const field_spec &handle::field(std::uint16_t index) const noexcept { return s_->fields[index]; }
inline type_view handle::field_type(std::uint16_t index) const noexcept {
    return {&s_->types, s_->types.field(index)};
}
inline std::span<const std::byte> handle::types_table() const noexcept { return s_->types.bytes(); }
inline std::uint32_t handle::record_size() const noexcept { return s_->record_size; }
inline std::uint64_t handle::schema_hash() const noexcept { return s_->schema_hash; }
inline std::uint64_t handle::create_id() const noexcept { return s_->create_id; }
inline std::uint16_t handle::waiter_slots() const noexcept { return s_->waiter_slots; }
inline std::uint16_t handle::minor_version() const noexcept { return s_->layout_minor; }
inline std::uint16_t handle::major_version() const noexcept { return s_->layout_major; }
inline void *handle::base() const noexcept { return s_->hdr; }
inline std::uint64_t handle::size() const noexcept { return s_->size; }

// Removes the name, as shm_unlink does: open handles keep working. Does nothing on Windows, where the
// OS frees the mapping with its last handle.
[[nodiscard]] inline result<void> unlink(std::string_view name) noexcept {
    if (!detail::name_ok(name))
        return unexpected(status::range);
#ifndef _WIN32
    const detail::object_name path = detail::make_name("/", name, "");
    if (shm_unlink(path.data()) != 0)
        return errno == ENOENT ? unexpected(status::not_found) : detail::os_failure();
#endif
    return {};
}

// A copy of line 0 of the segment called name, of any kind, read without waiting and without the checks
// of open: not_found when nothing under that name has published a header.
[[nodiscard]] inline result<common_header> inspect(std::string_view name) noexcept {
    if (!detail::name_ok(name))
        return unexpected(status::range);
    detail::backoff no_wait(0);
    result<detail::os_mapping> map = detail::map_open(name, no_wait);
    if (!map)
        return unexpected(map.error());
    auto *hdr = static_cast<header *>(map->base());
    if (detail::atomic(hdr->common.magic).load(std::memory_order_acquire) == 0)
        return unexpected(status::not_found);
    return detail::copy_common(hdr);
}

namespace detail {

// Runs attempt until it succeeds, pausing between tries; false once timeout seconds have passed. With
// Hooks, the wait hooks run only when the first try fails, before the first pause and after the last;
// the large copies run them around everything instead, so they pass false.
template <bool Hooks = true, class F> SHAREDBOX_HOT bool retry(const state &s, double timeout, F &&attempt) {
    if (attempt())
        return true;
    void *hook = nullptr;
    if constexpr (Hooks)
        hook = s.before_wait != nullptr ? s.before_wait() : nullptr;
    backoff wait(timeout);
    bool done = false;
    while (!wait.expired()) {
        wait.pause();
        if (attempt()) {
            done = true;
            break;
        }
    }
    if constexpr (Hooks)
        if (s.after_wait != nullptr)
            s.after_wait(hook);
    return done;
}

// Values of at least this many bytes are copied with read_large and write_large, which run the wait
// hooks around the whole copy, so a caller that releases a lock in them does so for the copy too.
inline constexpr std::size_t large_copy = std::size_t{1} << 20;
inline constexpr std::size_t copy_chunk = std::size_t{4} << 20;

// Copies n bytes while seq stays at before, a chunk at a time; false as soon as it moved, so a reader
// does not finish a copy it would throw away.
inline bool copy_checked(std::byte *dst, const std::byte *src, std::size_t n, std::atomic_ref<std::uint64_t> seq,
                         std::uint64_t before) noexcept {
    for (std::size_t done = 0; done < n;) {
        const std::size_t step = (std::min)(copy_chunk, n - done);
        std::memcpy(dst + done, src + done, step);
        done += step;
        std::atomic_thread_fence(std::memory_order_acquire);
        if (seq.load(std::memory_order_acquire) != before)
            return false;
    }
    return true;
}

// Runs before_wait now and after_wait when it goes out of scope.
class hooks_around {
public:
    explicit hooks_around(const state &s) noexcept
        : s_(s), hook_(s.before_wait != nullptr ? s.before_wait() : nullptr) {}
    ~hooks_around() {
        if (s_.after_wait != nullptr)
            s_.after_wait(hook_);
    }
    hooks_around(const hooks_around &) = delete;
    hooks_around &operator=(const hooks_around &) = delete;

private:
    const state &s_;
    void *hook_;
};

} // namespace detail

inline result<void> handle::set_lock_timeout(seconds timeout) noexcept {
    if (!detail::timeout_ok(timeout, false))
        return unexpected(status::range);
    s_->lock_timeout = timeout.count();
    return {};
}

inline void handle::set_wait_hooks(void *(*before)(), void (*after)(void *)) noexcept {
    s_->before_wait = before;
    s_->after_wait = after;
}

SHAREDBOX_HOT result<read_value> handle::read(std::uint16_t field, std::span<std::byte> buf) const {
    const detail::state &s = *s_;
    if (field >= s.field_count)
        return unexpected(status::range);
    const field_spec &f = s.fields[field];
    auto seq = detail::atomic(s.hdr->seq);
    read_value out{0, 0};
    const bool done = detail::retry(s, s.lock_timeout, [&]() noexcept {
        const std::uint64_t before = seq.load(std::memory_order_acquire);
        if ((before & 1u) != 0)
            return false;
        const std::span<const std::byte> src = detail::payload(f, s.record);
        if (!src.empty() && src.size() <= buf.size())
            std::memcpy(buf.data(), src.data(), src.size());
        const std::uint64_t version = detail::atomic(s.counts[field]).load(std::memory_order_relaxed);
        std::atomic_thread_fence(std::memory_order_acquire);
        if (seq.load(std::memory_order_acquire) != before)
            return false;
        out = {src.size(), version};
        return true;
    });
    if (!done)
        return unexpected(status::lock_timeout);
    return out;
}

inline result<std::uint64_t> handle::read_record(std::span<std::byte> buf) const {
    const detail::state &s = *s_;
    if (buf.size() < s.record_size)
        return unexpected(status::range);
    auto seq = detail::atomic(s.hdr->seq);
    std::uint64_t generation = 0;
    const bool done = detail::retry(s, s.lock_timeout, [&]() noexcept {
        const std::uint64_t before = seq.load(std::memory_order_acquire);
        if ((before & 1u) != 0)
            return false;
        std::memcpy(buf.data(), s.record, s.record_size);
        std::atomic_thread_fence(std::memory_order_acquire);
        if (seq.load(std::memory_order_acquire) != before)
            return false;
        generation = before >> 1;
        return true;
    });
    if (!done)
        return unexpected(status::lock_timeout);
    return generation;
}

namespace detail {

struct no_hooks {
    explicit no_hooks(const state &) noexcept {}
};

// read_used for one field; Large runs the wait hooks once around the copy and checks the sequence
// number between chunks.
template <bool Large>
result<read_value> read_used_impl(const state &s, std::uint16_t field, std::span<std::byte> buf) {
    const field_spec &f = s.fields[field];
    const type_node &node = s.types.node(s.types.field(field).node);
    auto seq = atomic(s.hdr->seq);
    read_value out{0, 0};
    const std::conditional_t<Large, hooks_around, no_hooks> hooks(s);
    const bool done = retry<!Large>(s, s.lock_timeout, [&]() noexcept {
        const std::uint64_t before = seq.load(std::memory_order_acquire);
        if ((before & 1u) != 0)
            return false;
        const std::byte *src = s.record + f.offset;
        std::uint32_t length;
        std::memcpy(&length, src, sizeof length);
        // A torn read may see any length; the sequence check below throws that copy away.
        if (length > node.capacity)
            length = node.capacity;
        const std::size_t used = node.slots + std::size_t{length} * node.stride;
        if (used <= buf.size()) {
            if constexpr (Large) {
                if (!copy_checked(buf.data(), src, used, seq, before))
                    return false;
            } else {
                std::memcpy(buf.data(), src, used);
            }
        }
        const std::uint64_t version = atomic(s.counts[field]).load(std::memory_order_relaxed);
        std::atomic_thread_fence(std::memory_order_acquire);
        if (seq.load(std::memory_order_acquire) != before)
            return false;
        out = {used, version};
        return true;
    });
    if (!done)
        return unexpected(status::lock_timeout);
    return out;
}

} // namespace detail

inline result<read_value> handle::read_used(std::uint16_t field, std::span<std::byte> buf) const {
    const detail::state &s = *s_;
    if (field >= s.field_count || !detail::is_collection(s.fields[field].kind))
        return unexpected(status::range);
    const detail::type_node &node = s.types.node(s.types.field(field).node);
    if (node.slots + std::uint64_t{node.capacity} * node.stride >= detail::large_copy)
        return detail::read_used_impl<true>(s, field, buf);
    return detail::read_used_impl<false>(s, field, buf);
}

inline result<read_value> handle::read_large(std::uint16_t field, std::span<std::byte> buf) const {
    const detail::state &s = *s_;
    if (field >= s.field_count)
        return unexpected(status::range);
    const field_spec &f = s.fields[field];
    auto seq = detail::atomic(s.hdr->seq);
    read_value out{0, 0};
    const detail::hooks_around hooks(s);
    const bool done = detail::retry<false>(s, s.lock_timeout, [&]() noexcept {
        const std::uint64_t before = seq.load(std::memory_order_acquire);
        if ((before & 1u) != 0)
            return false;
        const std::span<const std::byte> src = detail::payload(f, s.record);
        if (src.size() <= buf.size() && !detail::copy_checked(buf.data(), src.data(), src.size(), seq, before))
            return false;
        const std::uint64_t version = detail::atomic(s.counts[field]).load(std::memory_order_relaxed);
        std::atomic_thread_fence(std::memory_order_acquire);
        if (seq.load(std::memory_order_acquire) != before)
            return false;
        out = {src.size(), version};
        return true;
    });
    if (!done)
        return unexpected(status::lock_timeout);
    return out;
}

inline result<std::uint64_t> handle::read_record_large(std::span<std::byte> buf) const {
    const detail::state &s = *s_;
    if (buf.size() < s.record_size)
        return unexpected(status::range);
    auto seq = detail::atomic(s.hdr->seq);
    std::uint64_t generation = 0;
    const detail::hooks_around hooks(s);
    const bool done = detail::retry<false>(s, s.lock_timeout, [&]() noexcept {
        const std::uint64_t before = seq.load(std::memory_order_acquire);
        if ((before & 1u) != 0 || !detail::copy_checked(buf.data(), s.record, s.record_size, seq, before))
            return false;
        generation = before >> 1;
        return true;
    });
    if (!done)
        return unexpected(status::lock_timeout);
    return generation;
}

inline result<void> handle::write_large(std::span<const value> values, seconds lock_timeout) {
    detail::state &s = *s_;
    if (!detail::values_ok(s, values))
        return unexpected(status::range);
    const detail::hooks_around hooks(s);
    const result<std::uint64_t> locked = lock_with<false>(lock_timeout);
    if (!locked)
        return unexpected(locked.error());
    for (const value &v : values) {
        detail::store(s, v);
        auto count = detail::atomic(s.counts[v.field]);
        count.store(count.load(std::memory_order_relaxed) + 1, std::memory_order_relaxed);
    }
    unlock(*locked);
    detail::wake_waiters(s);
    return {};
}

inline std::span<const std::byte> handle::payload(std::uint16_t field,
                                                  std::span<const std::byte> record) const noexcept {
    return detail::payload(s_->fields[field], record.data());
}

template <bool Hooks> SHAREDBOX_HOT result<std::uint64_t> handle::lock_with(seconds lock_timeout) {
    detail::state &s = *s_;
    if (!detail::timeout_ok(lock_timeout, false))
        return unexpected(status::range);
    auto seq = detail::atomic(s.hdr->seq);
    std::uint64_t locked = 0;
    const bool done = detail::retry<Hooks>(s, lock_timeout.count(), [&]() noexcept {
        std::uint64_t even = seq.load(std::memory_order_relaxed);
        if ((even & 1u) != 0 ||
            !seq.compare_exchange_strong(even, even + 1, std::memory_order_acquire, std::memory_order_relaxed))
            return false;
        // Keeps the record stores that follow from moving before the odd sequence number.
        std::atomic_thread_fence(std::memory_order_release);
        detail::atomic(s.hdr->writer_pid).store(detail::current_pid(), std::memory_order_relaxed);
        locked = even;
        return true;
    });
    if (!done)
        return unexpected(status::lock_timeout);
    return locked;
}

SHAREDBOX_HOT result<std::uint64_t> handle::lock(seconds lock_timeout) { return lock_with<true>(lock_timeout); }

SHAREDBOX_HOT void handle::unlock(std::uint64_t locked) noexcept {
    header &h = *s_->hdr;
    // A force_unlock landing between this check and the swap can leave writer_pid cleared; only the
    // message of a later lock timeout depends on it.
    if (detail::atomic(h.seq).load(std::memory_order_relaxed) == locked + 1)
        detail::atomic(h.writer_pid).store(0, std::memory_order_relaxed);
    // If a force_unlock has released this writer's lock, seq has moved on and may belong to another
    // writer's lock, so the swap fails and changes nothing.
    std::uint64_t expected = locked + 1;
    detail::atomic(h.seq).compare_exchange_strong(expected, locked + 2, std::memory_order_release,
                                                  std::memory_order_relaxed);
}

inline result<field_write> handle::begin_write(std::uint16_t field, seconds lock_timeout) {
    detail::state &s = *s_;
    if (field >= s.field_count || s.fields[field].kind != kind_array)
        return unexpected(status::range);
    const result<std::uint64_t> locked = lock(lock_timeout);
    if (!locked)
        return unexpected(locked.error());
    const field_spec &f = s.fields[field];
    return field_write{field, {s.record + f.offset, f.capacity}, *locked};
}

inline void handle::end_write(const field_write &w) noexcept {
    detail::state &s = *s_;
    // Only the lock holder changes a count, so a plain store of the sum is enough.
    auto count = detail::atomic(s.counts[w.field]);
    count.store(count.load(std::memory_order_relaxed) + 1, std::memory_order_relaxed);
    unlock(w.locked);
    detail::wake_waiters(s);
}

SHAREDBOX_HOT result<void> handle::write(std::span<const value> values, seconds lock_timeout) {
    detail::state &s = *s_;
    if (!detail::values_ok(s, values))
        return unexpected(status::range);
    const result<std::uint64_t> locked = lock(lock_timeout);
    if (!locked)
        return unexpected(locked.error());
    for (const value &v : values) {
        detail::store(s, v);
        // Only the lock holder changes a count, so a plain store of the sum is enough.
        auto count = detail::atomic(s.counts[v.field]);
        count.store(count.load(std::memory_order_relaxed) + 1, std::memory_order_relaxed);
    }
    unlock(*locked);
    detail::wake_waiters(s);
    return {};
}

inline std::uint64_t handle::generation() const noexcept {
    return detail::atomic(s_->hdr->seq).load(std::memory_order_acquire) >> 1;
}

inline std::uint64_t handle::version(std::uint16_t field) const noexcept {
    return detail::atomic(s_->counts[field]).load(std::memory_order_acquire);
}

inline std::uint32_t handle::writer_pid() const noexcept {
    return detail::atomic(s_->hdr->writer_pid).load(std::memory_order_relaxed);
}

inline result<void> handle::force_unlock() noexcept {
    auto seq = detail::atomic(s_->hdr->seq);
    std::uint64_t odd = seq.load(std::memory_order_relaxed);
    if ((odd & 1u) != 0)
        seq.compare_exchange_strong(odd, odd + 1, std::memory_order_release, std::memory_order_relaxed);
    return {};
}

namespace detail {

inline bool owned_test(const state &s, std::uint16_t i) noexcept {
    return (s.owned[i / 64].load(std::memory_order_relaxed) >> (i % 64) & 1u) != 0;
}

inline void owned_set(state &s, std::uint16_t i) noexcept {
    s.owned[i / 64].fetch_or(std::uint64_t{1} << (i % 64), std::memory_order_acq_rel);
}

// Returns whether the bit was set.
inline bool owned_clear(state &s, std::uint16_t i) noexcept {
    const std::uint64_t bit = std::uint64_t{1} << (i % 64);
    return (s.owned[i / 64].fetch_and(~bit, std::memory_order_acq_rel) & bit) != 0;
}

// Whether slot i still records this process: a slot freed by mistake and claimed by another process
// is left to its new owner.
inline bool slot_is_mine(const state &s, std::uint16_t i) noexcept {
    waiter_slot &w = s.slots[i];
    return atomic(w.owner_pid).load(std::memory_order_acquire) == current_pid() &&
           atomic(w.owner_start).load(std::memory_order_acquire) == current_start() &&
           atomic(w.owner_pidns).load(std::memory_order_acquire) == current_pidns();
}

// owner_start while another process frees a dead owner's slot; no real start time has this value.
inline constexpr std::uint64_t start_freeing = UINT64_MAX - 1;

// Frees slot i, which this process claimed and stamped. owner_start goes first, so a process killed at a
// later step leaves no stamped slot for a freer to decrement again. Killed before the pidns store, it leaves an
// unstamped slot of an exited process, which a freer frees without a decrement (the count stays one too high
// if the kill came before the fetch_sub). Killed between the pidns and pid stores, it leaves namespace 0,
// which on Linux counts as alive: the slot is never freed, and the count stays correct. A claimer killed
// between its pid compare-and-swap and its pidns store also leaves namespace 0 and a slot never freed on
// Linux, counted once too many if it had already added itself to waiters.
inline void free_slot(const state &s, std::uint16_t i) noexcept {
    waiter_slot &w = s.slots[i];
    atomic(w.owner_start).store(0, std::memory_order_release);
    atomic(s.hdr->waiters).fetch_sub(1, std::memory_order_seq_cst);
    atomic(w.owner_pidns).store(0, std::memory_order_relaxed);
    atomic(w.owner_pid).store(0, std::memory_order_release);
}

// Does nothing; free_dead_slot calls it after each store so a test can run other steps in between.
struct no_pause {
    void operator()() const noexcept {}
};

// Frees slot w of the exited process pid, seen with owner_start start. Only the process whose compare-and-swap
// sets owner_start to start_freeing goes on, and scanners skip the slot while it holds that value, which it
// keeps until owner_pid is 0; a new claimer's stamp may replace it after that. A start of 0 is a claimer that
// died before stamping: it may or may not have counted itself in waiters, so the slot is freed without a
// decrement and the count can only be too high, which costs a spurious wake-up.
//
// Known limits: a freer killed before its owner_pid store leaves the slot unusable until the segment is created
// again, and counted once too many if it was stamped and the freer was killed before its fetch_sub. The check that
// owner_pid is still pid cannot tell an unstamped claimer from another one with the same pid, which needs the pid
// to be reused within a few instructions.
template <class Pause = no_pause>
inline void free_dead_slot(const state &s, waiter_slot &w, std::uint32_t pid, std::uint64_t start,
                           Pause pause = {}) noexcept {
    if (!atomic(w.owner_start)
             .compare_exchange_strong(start, start_freeing, std::memory_order_acq_rel, std::memory_order_relaxed))
        return;
    // The slot changed hands after the caller read pid. Mainly a stale read, start being the new owner's,
    // which free_dead_waiters's second read of owner_pid narrows; otherwise a new owner with the same start
    // time or none stamped yet. The slot is that owner's.
    if (atomic(w.owner_pid).load(std::memory_order_acquire) != pid) {
        std::uint64_t freeing = start_freeing;
        atomic(w.owner_start)
            .compare_exchange_strong(freeing, start, std::memory_order_release, std::memory_order_relaxed);
        return;
    }
    if (start != 0) {
        atomic(s.hdr->waiters).fetch_sub(1, std::memory_order_seq_cst);
        pause();
    }
    atomic(w.owner_pidns).store(0, std::memory_order_relaxed);
    pause();
    atomic(w.owner_pid).store(0, std::memory_order_release);
    pause();
    // Fails, harmlessly, once a new claimer has stamped its own start.
    std::uint64_t freeing = start_freeing;
    atomic(w.owner_start)
        .compare_exchange_strong(freeing, 0, std::memory_order_release, std::memory_order_relaxed);
    pause();
}

// Frees every slot whose owner has exited.
inline void free_dead_waiters(const state &s) noexcept {
    const std::uint32_t self = current_pid();
    const std::uint64_t own_pidns = current_pidns();
    for (std::uint16_t i = 0; i < s.waiter_slots; ++i) {
        waiter_slot &w = s.slots[i];
        const std::uint32_t pid = atomic(w.owner_pid).load(std::memory_order_acquire);
        if (pid == 0)
            continue;
        std::uint64_t start = atomic(w.owner_start).load(std::memory_order_acquire);
#ifdef _WIN32
        static_cast<void>(own_pidns);
        const bool checkable = true;
#else
        // A pid means something only inside its namespace: a slot of another namespace, or with either
        // namespace unknown (0), counts as alive. Windows has no pid namespaces.
        const std::uint64_t pidns = atomic(w.owner_pidns).load(std::memory_order_acquire);
        const bool checkable = pidns != 0 && own_pidns != 0 && pidns == own_pidns;
#endif
        // The slot changed hands between the reads: start and pidns may be the new owner's, not pid's.
        if (atomic(w.owner_pid).load(std::memory_order_acquire) != pid)
            continue;
        // start 0: not stamped yet, so only whether the pid runs at all can be checked.
        if (start == start_freeing || !checkable || (pid == self && (start == 0 || start == current_start())) ||
            process_alive(pid, start == 0 ? start_unknown : start))
            continue;
        free_dead_slot(s, w, pid, start);
    }
}

} // namespace detail

inline result<std::uint16_t> handle::register_waiter() {
    detail::state &s = *s_;
    const std::uint32_t pid = detail::current_pid();
    const std::uint64_t start = detail::current_start();
    const std::uint64_t pidns = detail::current_pidns();
    detail::free_dead_waiters(s);
    for (std::uint16_t i = 0; i < s.waiter_slots; ++i) {
        waiter_slot &w = s.slots[i];
        std::uint32_t none = 0;
        if (!detail::atomic(w.owner_pid)
                 .compare_exchange_strong(none, pid, std::memory_order_acq_rel, std::memory_order_relaxed))
            continue;
        // Counted before owner_start is stamped: a freer decrements only for a stamped slot, so a claimer
        // killed in between leaves waiters too high, never too low.
        detail::atomic(s.hdr->waiters).fetch_add(1, std::memory_order_seq_cst);
        detail::atomic(w.interrupt).store(0, std::memory_order_relaxed);
        detail::atomic(w.owner_pidns).store(pidns, std::memory_order_relaxed);
        detail::atomic(w.owner_start).store(start, std::memory_order_release);
        detail::owned_set(s, i);
#ifdef _WIN32
        HANDLE e = detail::event(s, i);
        if (e == nullptr) {
            const unexpected failed = detail::os_failure();
            detail::owned_clear(s, i);
            detail::free_slot(s, i);
            return failed;
        }
        // Drops a wake-up meant for an earlier owner of the slot.
        ResetEvent(e);
#endif
        return i;
    }
    return unexpected(status::no_slot);
}

inline void handle::release_waiter(std::uint16_t slot) noexcept {
    detail::state &s = *s_;
    if (slot < s.waiter_slots && detail::owned_clear(s, slot) && detail::slot_is_mine(s, slot))
        detail::free_slot(s, slot);
}

inline bool handle::waiter_held(std::uint16_t slot) const noexcept {
    return slot < s_->waiter_slots && detail::owned_test(*s_, slot) && detail::slot_is_mine(*s_, slot);
}

inline std::uint32_t handle::waiters() const noexcept {
    return detail::atomic(s_->hdr->waiters).load(std::memory_order_acquire);
}

namespace detail {

#ifdef _WIN32
// The auto-reset event of waiter slot i, opened on first use and kept until the handle is destroyed.
inline HANDLE event(const state &s, std::uint16_t slot) noexcept {
    std::atomic<HANDLE> &cell = s.events[slot];
    HANDLE e = cell.load(std::memory_order_acquire);
    if (e != nullptr)
        return e;
    char suffix[8];
    std::snprintf(suffix, sizeof suffix, "#w%u", static_cast<unsigned>(slot));
    const wide_name wide = make_wide_name(s.name, suffix);
    e = CreateEventW(nullptr, FALSE, FALSE, wide.data());
    if (e == nullptr)
        return nullptr;
    HANDLE previous = nullptr;
    if (!cell.compare_exchange_strong(previous, e, std::memory_order_acq_rel, std::memory_order_acquire)) {
        CloseHandle(e);
        return previous;
    }
    return e;
}
#endif

SHAREDBOX_HOT void wake_waiters(const state &s) noexcept {
    header &h = *s.hdr;
    // seq_cst, not only release: the load of waiters below must not move before this increment or the
    // swap of seq, or a waiter registering at the same time could be missed.
    atomic(h.wake_word).fetch_add(1, std::memory_order_seq_cst);
    // waiters is never below the number of claimed slots, so 0 means no one to wake.
    if (atomic(h.waiters).load(std::memory_order_seq_cst) == 0)
        return;
#ifdef _WIN32
    for (std::uint16_t i = 0; i < s.waiter_slots; ++i)
        if (atomic(s.slots[i].owner_pid).load(std::memory_order_seq_cst) != 0)
            if (HANDLE e = event(s, i); e != nullptr)
                SetEvent(e);
#else
    syscall(SYS_futex, &h.wake_word, futex_wake, INT_MAX, nullptr, nullptr, 0);
#endif
}

} // namespace detail

inline result<wake> handle::wait(std::uint16_t slot, std::uint64_t last_generation, seconds timeout) {
    detail::state &s = *s_;
    if (slot >= s.waiter_slots || !detail::owned_test(s, slot) || !detail::timeout_ok(timeout, true))
        return unexpected(status::range);
    header &h = *s.hdr;
    const detail::clock::time_point deadline =
        detail::clock::now() + std::chrono::duration_cast<detail::clock::duration>(timeout);
    for (;;) {
        // The word is read before the generation: a write between the two changes the word, and the
        // futex then refuses to sleep.
        const std::uint32_t word = detail::atomic(h.wake_word).load(std::memory_order_seq_cst);
        if (detail::atomic(h.seq).load(std::memory_order_seq_cst) >> 1 != last_generation)
            return wake::changed;
        std::uint32_t set = 1;
        if (detail::atomic(s.slots[slot].interrupt)
                .compare_exchange_strong(set, 0, std::memory_order_acq_rel, std::memory_order_relaxed))
            return wake::interrupted;
        const detail::clock::duration remaining = deadline - detail::clock::now();
        if (remaining <= detail::clock::duration::zero())
            return unexpected(status::timeout);
#ifdef _WIN32
        static_cast<void>(word);
        HANDLE e = detail::event(s, slot);
        if (e == nullptr)
            return detail::os_failure();
        const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(remaining).count();
        if (WaitForSingleObject(e, static_cast<DWORD>(ms) + 1) == WAIT_FAILED)
            return detail::os_failure();
#else
        const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(remaining).count();
        timespec ts{};
        ts.tv_sec = static_cast<time_t>(ns / 1000000000);
        ts.tv_nsec = static_cast<long>(ns % 1000000000);
        syscall(SYS_futex, &h.wake_word, detail::futex_wait, word, &ts, nullptr, 0);
#endif
    }
}

inline result<void> handle::interrupt(std::uint16_t slot) {
    detail::state &s = *s_;
    if (slot >= s.waiter_slots)
        return unexpected(status::range);
    detail::atomic(s.slots[slot].interrupt).store(1, std::memory_order_seq_cst);
#ifdef _WIN32
    HANDLE e = detail::event(s, slot);
    if (e == nullptr)
        return detail::os_failure();
    SetEvent(e);
#else
    detail::atomic(s.hdr->wake_word).fetch_add(1, std::memory_order_seq_cst);
    syscall(SYS_futex, &s.hdr->wake_word, detail::futex_wake, INT_MAX, nullptr, nullptr, 0);
#endif
    return {};
}

namespace detail {

inline void release_owned(sbx_handle *h) noexcept {
    delete static_cast<handle *>(h->private_data);
    h->release = nullptr;
    h->private_data = nullptr;
    h->base = nullptr;
}

// Moves h to the heap and fills out with a struct that owns it. On failure h is left as it was.
inline status export_into(handle &&h, sbx_handle &out) noexcept {
    auto *owner = new (std::nothrow) handle(std::move(h));
    if (owner == nullptr)
        return status::os;
    out.layout_major = owner->major_version();
    out.layout_minor = owner->minor_version();
    out.handle_version = handle_version;
    out.base = owner->base();
    out.size = owner->size();
    out.name = owner->name().data();
    out.release = release_owned;
    out.private_data = owner;
    return status::ok;
}

} // namespace detail

inline result<handle> handle::from_capsule(sbx_handle *capsule) {
    if (capsule == nullptr || capsule->release == nullptr || capsule->base == nullptr ||
        capsule->handle_version < 1 || capsule->name == nullptr)
        return unexpected(status::range);
    std::size_t length = 0;
    while (length <= name_max && capsule->name[length] != '\0')
        ++length;
    const std::string_view name(capsule->name, length);
    if (!detail::name_ok(name))
        return unexpected(status::range);
    auto *hdr = static_cast<header *>(capsule->base);
    if (capsule->size < page_size || detail::atomic(hdr->common.magic).load(std::memory_order_acquire) == 0)
        return unexpected(status::corrupt);
    const result<header> opened = detail::open_header(hdr, capsule->size);
    if (!opened)
        return unexpected(opened.error());
    const header &h = *opened;
    std::unique_ptr<detail::state> s = detail::make_state(name, h.field_count, h.common.waiter_slots);
    if (s == nullptr)
        return unexpected(status::os);
    s->record_size = h.record_size;
    if (const status rc = detail::copy_fields(*s, hdr, h); rc != status::ok)
        return unexpected(rc);
    s->record_offset = h.record;
    s->schema_hash = h.common.schema_hash;
    s->create_id = h.common.create_id;
    s->foreign = *capsule;
    capsule->release = nullptr;
    detail::bind(*s, s->foreign.base, s->foreign.size, h.common.kind_major, h.common.kind_minor);
    return handle(s.release());
}

inline result<handle> handle::duplicate() const {
    const detail::state &src = *s_;
    if (src.foreign.release != nullptr)
        return unexpected(status::range);
#ifdef _WIN32
    HANDLE os = nullptr;
    if (!DuplicateHandle(GetCurrentProcess(), src.map.os(), GetCurrentProcess(), &os, 0, FALSE,
                         DUPLICATE_SAME_ACCESS))
        return detail::os_failure();
    void *base = MapViewOfFile(os, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, 0);
    if (base == nullptr) {
        const unexpected failed = detail::os_failure();
        CloseHandle(os);
        return failed;
    }
#else
    const int os = fcntl(src.map.os(), F_DUPFD_CLOEXEC, 0);
    if (os < 0)
        return detail::os_failure();
    void *base = mmap(nullptr, static_cast<std::size_t>(src.size), PROT_READ | PROT_WRITE, MAP_SHARED, os, 0);
    if (base == MAP_FAILED) {
        const unexpected failed = detail::os_failure();
        close(os);
        return failed;
    }
#endif
    detail::os_mapping map = detail::os_mapping::adopt(os, base, src.size);
    std::unique_ptr<detail::state> s = detail::make_state(src.name, src.field_count, src.waiter_slots);
    if (s == nullptr)
        return unexpected(status::os);
    std::copy_n(src.fields.get(), src.field_count, s->fields.get());
    if (const status rc = src.types.copy(s->types); rc != status::ok)
        return unexpected(rc);
    s->record_size = src.record_size;
    s->record_offset = src.record_offset;
    s->schema_hash = src.schema_hash;
    s->create_id = src.create_id;
    s->lock_timeout = src.lock_timeout;
    s->map = std::move(map);
    detail::bind(*s, s->map.base(), src.size, src.layout_major, src.layout_minor);
    return handle(s.release());
}

inline sbx_handle *handle::to_capsule() && {
    std::unique_ptr<sbx_handle> out(new (std::nothrow) sbx_handle{});
    if (out == nullptr || detail::export_into(std::move(*this), *out) != status::ok)
        return nullptr;
    return out.release();
}

} // namespace v3
} // namespace sharedbox

#endif
