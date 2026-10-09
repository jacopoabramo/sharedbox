// stream.hpp: layout 1.0 of a stream segment, a ring that one sender writes and readers copy out of.
#ifndef SHAREDBOX_STREAM_HPP
#define SHAREDBOX_STREAM_HPP

#include <sharedbox/core.hpp>

namespace sharedbox {
inline namespace v3 {

inline constexpr std::uint16_t stream_layout_major = 1;
inline constexpr std::uint16_t stream_layout_minor = 0;
inline constexpr std::uint32_t stream_header_size = 256;
inline constexpr std::uint64_t min_stream_capacity = 2;
// Each reader and the sender hold one waiter slot.
inline constexpr std::uint32_t max_stream_readers = max_waiter_slots - 1;
// The slots and the description table end below this, so no sum of offsets and sizes overflows.
inline constexpr std::uint64_t max_stream_size = std::uint64_t{1} << 46;
inline constexpr std::uint32_t stream_open = 0;
inline constexpr std::uint32_t stream_ended = 1;

enum class read_mode : std::uint32_t { lossless = 1, lossy = 2, latest = 3 };
enum class start_at { newest, oldest };

// A stream's header: the common line 0; line 1, written once by the creator; line 2, which the sender
// changes (and readers_epoch, which readers change); line 3, which readers change.
struct stream_header {
    common_header common;
    std::uint64_t capacity;
    std::uint64_t slot_size;
    std::uint64_t readers;
    std::uint64_t slots;
    std::uint64_t types;
    std::uint32_t types_size;
    std::uint32_t item_entry;
    std::uint32_t max_readers;
    std::uint32_t reserved1;
    std::uint64_t reserved2;
    std::uint64_t write_pos;
    std::uint64_t sender_start;
    std::uint64_t sender_pidns;
    std::uint32_t sender_pid;
    std::uint32_t state;
    std::uint32_t data_word;
    std::uint32_t readers_epoch;
    std::uint32_t space_waiting;
    std::uint8_t reserved3[20];
    std::uint32_t space_word;
    std::uint32_t lossless_readers;
    std::uint32_t data_waiting;
    std::uint32_t waiters;
    std::uint8_t reserved4[48];
};

// One line of the reader table. owner_pid is 0 when the entry is free, mode 0 until its reader has a
// position.
struct reader_entry {
    std::uint64_t position;
    std::uint64_t owner_start;
    std::uint64_t owner_pidns;
    std::uint32_t owner_pid;
    std::uint32_t mode;
    std::uint8_t reserved[32];
};

static_assert(std::is_standard_layout_v<stream_header> && std::is_trivially_copyable_v<stream_header>);
static_assert(std::is_standard_layout_v<reader_entry> && std::is_trivially_copyable_v<reader_entry>);
static_assert(sizeof(stream_header) == stream_header_size && alignof(stream_header) == 8);
static_assert(offsetof(stream_header, common) == 0 && offsetof(stream_header, capacity) == 64 &&
              offsetof(stream_header, slot_size) == 72 && offsetof(stream_header, readers) == 80 &&
              offsetof(stream_header, slots) == 88 && offsetof(stream_header, types) == 96 &&
              offsetof(stream_header, types_size) == 104 && offsetof(stream_header, item_entry) == 108 &&
              offsetof(stream_header, max_readers) == 112 && offsetof(stream_header, reserved2) == 120);
static_assert(offsetof(stream_header, write_pos) == 128 && offsetof(stream_header, sender_start) == 136 &&
              offsetof(stream_header, sender_pidns) == 144 && offsetof(stream_header, sender_pid) == 152 &&
              offsetof(stream_header, state) == 156 && offsetof(stream_header, data_word) == 160 &&
              offsetof(stream_header, readers_epoch) == 164 && offsetof(stream_header, space_waiting) == 168);
static_assert(offsetof(stream_header, space_word) == 192 && offsetof(stream_header, lossless_readers) == 196 &&
              offsetof(stream_header, data_waiting) == 200 && offsetof(stream_header, waiters) == 204);
static_assert(sizeof(reader_entry) == 64 && alignof(reader_entry) == 8);
static_assert(offsetof(reader_entry, position) == 0 && offsetof(reader_entry, owner_start) == 8 &&
              offsetof(reader_entry, owner_pidns) == 16 && offsetof(reader_entry, owner_pid) == 24 &&
              offsetof(reader_entry, mode) == 28);
static_assert(offsetof(stream_header, write_pos) % detail::align64 == 0 &&
              offsetof(stream_header, sender_start) % detail::align64 == 0 &&
              offsetof(stream_header, sender_pidns) % detail::align64 == 0);
static_assert(offsetof(stream_header, sender_pid) % detail::align32 == 0 &&
              offsetof(stream_header, state) % detail::align32 == 0 &&
              offsetof(stream_header, data_word) % detail::align32 == 0 &&
              offsetof(stream_header, readers_epoch) % detail::align32 == 0 &&
              offsetof(stream_header, space_waiting) % detail::align32 == 0 &&
              offsetof(stream_header, space_word) % detail::align32 == 0 &&
              offsetof(stream_header, lossless_readers) % detail::align32 == 0 &&
              offsetof(stream_header, data_waiting) % detail::align32 == 0 &&
              offsetof(stream_header, waiters) % detail::align32 == 0);
static_assert(offsetof(reader_entry, owner_pid) % detail::align32 == 0 &&
              offsetof(reader_entry, mode) % detail::align32 == 0);

namespace detail {

// Where an item starts in its slot: after the slot's seq, at the item's own alignment.
inline std::uint64_t item_start(std::uint32_t alignment) noexcept {
    return round_up(sizeof(std::uint64_t), alignment);
}

// The offsets and mapping size of a stream of this shape. create writes them and open requires them.
struct stream_shape {
    std::uint64_t readers = 0;
    std::uint64_t waiters = 0;
    std::uint64_t slots = 0;
    std::uint64_t types = 0;
    std::uint64_t size = 0;
};

// False when the slots and the table would pass max_stream_size; max_readers must be at most
// max_stream_readers and types_size at most max_types_size.
inline bool stream_shape_of(std::uint64_t capacity, std::uint64_t slot_size, std::uint32_t max_readers,
                            std::uint32_t types_size, stream_shape &out) noexcept {
    out.readers = stream_header_size;
    out.waiters = out.readers + std::uint64_t{max_readers} * sizeof(reader_entry);
    out.slots = round_up(out.waiters + (std::uint64_t{max_readers} + 1) * sizeof(waiter_slot), 64);
    if (slot_size == 0 || capacity > (max_stream_size - out.slots) / slot_size)
        return false;
    out.types = out.slots + capacity * slot_size;
    out.size = round_up(out.types + types_size, page_size);
    return true;
}

struct stream_layout {
    static constexpr std::uint64_t magic = stream_magic;
    static constexpr std::uint16_t oldest_major = stream_layout_major;
    static constexpr std::uint16_t newest_major = stream_layout_major;
    using header_type = stream_header;
    // Lines 0 and 1. Lines 2 and 3 change while the stream is open, so the copy leaves them zero.
    static stream_header copy_header(const void *base) noexcept {
        stream_header h{};
        std::memcpy(&h, base, offsetof(stream_header, write_pos));
        return h;
    }
    // Line 1 of the copied header, after its line 0 passed the core checks. Every offset must be the
    // one create computes from the shape, so nothing else needs a range check.
    static status check_geometry(const stream_header &h, std::uint64_t mapped) noexcept {
        if (h.capacity < min_stream_capacity || h.max_readers == 0 || h.max_readers > max_stream_readers ||
            h.common.waiter_slots != h.max_readers + 1 || h.slot_size < 64 || h.slot_size % 64 != 0 ||
            h.types_size % 8 != 0 || h.types_size > max_types_size)
            return status::corrupt;
        stream_shape shape;
        if (!stream_shape_of(h.capacity, h.slot_size, h.max_readers, h.types_size, shape))
            return status::corrupt;
        if (h.readers != shape.readers || h.slots != shape.slots || h.types != shape.types || shape.size != mapped)
            return status::corrupt;
        return status::ok;
    }
};
static_assert(segment_layout<stream_layout>);

// What a stream object keeps: the checked copy of line 1 and the item type, pointers into the mapping,
// and the waiter table.
struct stream_state {
    char name[name_max + 1] = {};
    stream_header *hdr = nullptr;
    reader_entry *readers = nullptr;
    std::byte *slots = nullptr;
    std::uint64_t capacity = 0;
    std::uint64_t slot_size = 0;
    std::uint64_t item_offset = 0;
    std::uint64_t item_size = 0;
    std::uint64_t size = 0;
    std::uint64_t schema_hash = 0;
    std::uint64_t create_id = 0;
    std::uint32_t max_readers = 0;
    type_table types;
    os_mapping map;
    waiter_table waiters;

    ~stream_state() {
        // Before map unmaps the segment the slots live in.
        if (waiters.slots != nullptr)
            release_owned_slots(waiters);
    }
};

// A state for a stream with that many waiter slots, or nullptr when memory runs out.
inline std::unique_ptr<stream_state> make_stream_state(std::string_view name,
                                                       std::uint16_t waiter_slots) noexcept {
    std::unique_ptr<stream_state> s(new (std::nothrow) stream_state);
    if (s == nullptr)
        return nullptr;
#ifdef _WIN32
    s->waiters.events.reset(new (std::nothrow) std::atomic<HANDLE>[waiter_slots]());
    if (s->waiters.events == nullptr)
        return nullptr;
    s->waiters.name = s->name;
#else
    static_cast<void>(waiter_slots);
#endif
    std::memcpy(s->name, name.data(), name.size());
    return s;
}

// Takes the mapping into s, checks the item type of h, which passed the geometry check, and points s
// into the mapping. status::corrupt for an item of a kind this header does not know or a slot size
// that does not fit the item.
inline status bind_stream(stream_state &s, os_mapping &&map, const stream_header &h) noexcept {
    const auto *bytes = static_cast<const std::byte *>(map.base());
    const std::uint32_t entry = h.item_entry;
    if (const status rc = s.types.parse({bytes + h.types, h.types_size}, {&entry, 1}); rc != status::ok)
        return rc;
    const type_ref &item = s.types.field(0);
    if (!kind_known(item.kind) || round_up(item_start(item.alignment) + item.size, 64) != h.slot_size)
        return status::corrupt;
    s.map = std::move(map);
    auto *base = static_cast<std::byte *>(s.map.base());
    s.hdr = reinterpret_cast<stream_header *>(base);
    s.readers = reinterpret_cast<reader_entry *>(base + h.readers);
    s.slots = base + h.slots;
    s.capacity = h.capacity;
    s.slot_size = h.slot_size;
    s.item_offset = item_start(item.alignment);
    s.item_size = item.size;
    s.size = h.common.size;
    s.schema_hash = h.common.schema_hash;
    s.create_id = h.common.create_id;
    s.max_readers = h.max_readers;
    s.waiters.slots =
        reinterpret_cast<waiter_slot *>(base + h.readers + std::uint64_t{h.max_readers} * sizeof(reader_entry));
    s.waiters.claimed = &s.hdr->waiters;
    s.waiters.count = h.common.waiter_slots;
    return status::ok;
}

} // namespace detail

// A stream segment, mapped into this process. Move-only; the destructor releases it. Senders and
// readers made from it must not outlive it.
class stream {
public:
    stream() noexcept = default;
    stream(stream &&other) noexcept : s_(std::exchange(other.s_, nullptr)) {}
    stream &operator=(stream &&other) noexcept {
        stream(std::move(other)).swap(*this);
        return *this;
    }
    ~stream() { delete s_; }

    // Creates a stream of capacity slots whose item is described by item_entry (capacity_and_kind, as a
    // box field's) and types; status::range for a shape it cannot lay out.
    [[nodiscard]] static result<stream> create(std::string_view name, std::span<const std::byte> types,
                                               std::uint32_t item_entry, std::uint64_t capacity,
                                               std::uint32_t max_readers, std::uint64_t schema_hash);
    // Opens the stream called name, waiting up to timeout for a creator that has not finished. The caller
    // compares schema_hash() with its own.
    [[nodiscard]] static result<stream> open(std::string_view name, seconds timeout);

    std::string_view name() const noexcept { return s_->name; }
    std::uint64_t schema_hash() const noexcept { return s_->schema_hash; }
    std::uint64_t create_id() const noexcept { return s_->create_id; }
    std::uint64_t capacity() const noexcept { return s_->capacity; }
    std::uint32_t max_readers() const noexcept { return s_->max_readers; }
    // Bytes of one item, a length prefix included.
    std::uint64_t item_size() const noexcept { return s_->item_size; }
    // Valid while this stream lives.
    type_view item_type() const noexcept { return {&s_->types, s_->types.field(0)}; }
    std::span<const std::byte> types_table() const noexcept { return s_->types.bytes(); }
    void *base() const noexcept { return s_->hdr; }
    std::uint64_t size() const noexcept { return s_->size; }

private:
    explicit stream(detail::stream_state *s) noexcept : s_(s) {}
    void swap(stream &other) noexcept { std::swap(s_, other.s_); }

    detail::stream_state *s_ = nullptr;
};

inline result<stream> stream::create(std::string_view name, std::span<const std::byte> types,
                                     std::uint32_t item_entry, std::uint64_t capacity, std::uint32_t max_readers,
                                     std::uint64_t schema_hash) {
    if (!detail::name_ok(name) || capacity < min_stream_capacity || max_readers == 0 ||
        max_readers > max_stream_readers || types.size() > max_types_size || types.size() % 8 != 0)
        return unexpected(status::range);
    detail::type_table parsed;
    // A table the caller built wrong is its mistake, status::range; running out of memory is not.
    if (const status rc = parsed.parse(types, {&item_entry, 1}); rc != status::ok)
        return unexpected(rc == status::os ? status::os : status::range);
    const detail::type_ref &item = parsed.field(0);
    if (!detail::kind_known(item.kind))
        return unexpected(status::range);
    const std::uint64_t slot_size = detail::round_up(detail::item_start(item.alignment) + item.size, 64);
    const auto types_size = static_cast<std::uint32_t>(types.size());
    detail::stream_shape shape;
    if (!detail::stream_shape_of(capacity, slot_size, max_readers, types_size, shape))
        return unexpected(status::range);
    const auto waiter_slots = static_cast<std::uint16_t>(max_readers + 1);
    std::unique_ptr<detail::stream_state> s = detail::make_stream_state(name, waiter_slots);
    if (s == nullptr)
        return unexpected(status::os);
    const result<std::uint64_t> id = detail::random_id();
    if (!id)
        return unexpected(id.error());
    result<detail::os_mapping> map = detail::map_create(name, shape.size);
    if (!map)
        return unexpected(map.error());
    auto *base = static_cast<std::byte *>(map->base());
    stream_header &h = *reinterpret_cast<stream_header *>(base);
    h.common.core_major = core_major;
    h.common.core_minor = core_minor;
    h.common.kind_major = stream_layout_major;
    h.common.kind_minor = stream_layout_minor;
    h.common.schema_hash = schema_hash;
    h.common.create_id = *id;
    h.common.creator_start = detail::current_start();
    h.common.creator_pidns = detail::current_pidns();
    h.common.creator_pid = detail::current_pid();
    h.common.waiter_slots = waiter_slots;
    h.common.size = shape.size;
    h.capacity = capacity;
    h.slot_size = slot_size;
    h.readers = shape.readers;
    h.slots = shape.slots;
    h.types = shape.types;
    h.types_size = types_size;
    h.item_entry = item_entry;
    h.max_readers = max_readers;
    if (!types.empty())
        std::memcpy(base + shape.types, types.data(), types.size());
    if (const status rc = detail::bind_stream(*s, std::move(*map), detail::stream_layout::copy_header(base));
        rc != status::ok) {
        static_cast<void>(unlink(name));
        return unexpected(rc);
    }
    // Stored last, so a process that sees the magic sees everything above.
    detail::atomic(h.common.magic).store(stream_magic, std::memory_order_release);
    return stream(s.release());
}

inline result<stream> stream::open(std::string_view name, seconds timeout) {
    if (!detail::name_ok(name) || !detail::timeout_ok(timeout, false))
        return unexpected(status::range);
    detail::backoff wait(timeout.count());
    result<detail::os_mapping> map = detail::map_open(name, wait);
    if (!map)
        return unexpected(map.error());
    if (!detail::wait_published(map->base(), wait))
        return unexpected(status::not_found);
    const result<stream_header> opened = detail::open_header<detail::stream_layout>(map->base(), map->size());
    if (!opened)
        return unexpected(opened.error());
    std::unique_ptr<detail::stream_state> s = detail::make_stream_state(name, opened->common.waiter_slots);
    if (s == nullptr)
        return unexpected(status::os);
    if (const status rc = detail::bind_stream(*s, std::move(*map), *opened); rc != status::ok)
        return unexpected(rc);
    detail::free_dead_waiters(s->waiters);
    return stream(s.release());
}

} // namespace v3
} // namespace sharedbox

#endif
