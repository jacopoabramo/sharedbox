// Layout 1.0 of a stream: the header, the offsets create writes and open requires, and refusing a
// segment of another kind.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "table_builder.hpp"
#include "unique.hpp"

#include <memory>
#include <string>

using namespace sharedbox;

namespace {

// A uint8 array of n bytes: an item aligned to 64.
table_builder bytes_array(std::uint64_t n) {
    table_builder t;
    t.head(kind_array, 0, static_cast<std::uint32_t>(n));
    t.put(dl_dtype{1, 8, 1});
    t.put(std::uint8_t{1});
    t.put(std::uint8_t{0});
    t.put(std::uint16_t{0});
    t.put(n);
    t.pad();
    return t;
}

stream_header &header_of(const stream &s) { return *static_cast<stream_header *>(s.base()); }

status open_status(const std::string &name) {
    const auto opened = stream::open(name, seconds(0.2));
    return opened ? status::ok : opened.error().code;
}

} // namespace

TEST_CASE("a new stream is core 1.0, stream layout 1.0, and opens with the same shape") {
    const std::string name = unique("stream-new");
    auto made = stream::create(name, {}, entry_of(kind_int, 8), 4, 2, 0x5EED);
    REQUIRE(made);
    const stream_header &h = header_of(*made);
    CHECK(h.common.magic == stream_magic);
    CHECK(h.common.core_major == core_major);
    CHECK(h.common.kind_major == stream_layout_major);
    CHECK(h.common.waiter_slots == 3);
    CHECK(h.readers == stream_header_size);
    CHECK(h.slots % 64 == 0);
    CHECK(h.slot_size == 64);
    auto opened = stream::open(name, seconds(1.0));
    REQUIRE(opened);
    CHECK(opened->create_id() == made->create_id());
    CHECK(opened->schema_hash() == 0x5EED);
    CHECK(opened->capacity() == 4);
    CHECK(opened->max_readers() == 2);
    CHECK(opened->item_size() == 8);
    CHECK(opened->item_type().kind() == kind_int);
    static_cast<void>(unlink(name));
}

TEST_CASE("an item aligned to 64 bytes starts 64 bytes into its slot") {
    const std::string name = unique("stream-align");
    const table_builder t = bytes_array(32);
    auto made = stream::create(name, t.span(), entry_of(kind_array, 0), 4, 2, 1);
    REQUIRE(made);
    // 64 for the seq and padding, 32 for the item, rounded up to 64.
    CHECK(header_of(*made).slot_size == 128);
    static_cast<void>(unlink(name));
}

TEST_CASE("a stream opened as a box, and a box as a stream, is a kind mismatch naming what was found") {
    const std::string stream_name = unique("stream-as-box");
    auto st = stream::create(stream_name, {}, entry_of(kind_int, 8), 4, 2, 1);
    REQUIRE(st);
    const auto as_box = handle::open(stream_name, seconds(0.2));
    REQUIRE_FALSE(as_box);
    CHECK(as_box.error().code == status::kind_mismatch);
    CHECK(as_box.error().found == stream_magic);

    const std::string box_name = unique("box-as-stream");
    constexpr field_spec fields[1] = {{0, 8, kind_int}};
    auto box = handle::create(box_name, fields, 8, 1, 4, {});
    REQUIRE(box);
    const auto as_stream = stream::open(box_name, seconds(0.2));
    REQUIRE_FALSE(as_stream);
    CHECK(as_stream.error().code == status::kind_mismatch);
    CHECK(as_stream.error().found == box_magic);
    static_cast<void>(unlink(stream_name));
    static_cast<void>(unlink(box_name));
}

TEST_CASE("a stream header that does not match its mapping is refused") {
    const std::string name = unique("stream-forged");
    auto made = stream::create(name, {}, entry_of(kind_int, 8), 4, 2, 1);
    REQUIRE(made);
    stream_header &h = header_of(*made);
    const auto with = [&](auto &field, auto bad) {
        const auto saved = field;
        field = bad;
        const status rc = open_status(name);
        field = saved;
        return rc;
    };
    CHECK(open_status(name) == status::ok);
    CHECK(with(h.capacity, std::uint64_t{1}) == status::corrupt);
    CHECK(with(h.slot_size, std::uint64_t{63}) == status::corrupt);
    CHECK(with(h.slot_size, std::uint64_t{128}) == status::corrupt);
    CHECK(with(h.max_readers, std::uint32_t{0}) == status::corrupt);
    CHECK(with(h.common.waiter_slots, std::uint16_t{2}) == status::corrupt);
    CHECK(with(h.slots, h.slots + 64) == status::corrupt);
    CHECK(with(h.types, h.types + 8) == status::corrupt);
    CHECK(with(h.types_size, std::uint32_t{4}) == status::corrupt);
    CHECK(with(h.item_entry, entry_of(13, 8)) == status::corrupt);
    CHECK(with(h.common.kind_major, std::uint16_t{2}) == status::layout);
    static_cast<void>(unlink(name));
}

TEST_CASE("create refuses a stream it cannot lay out") {
    const std::string name = unique("stream-refused");
    const auto refused = [&](std::uint32_t entry, std::uint64_t capacity, std::uint32_t readers,
                             std::span<const std::byte> types = {}) {
        const auto made = stream::create(name, types, entry, capacity, readers, 1);
        return made ? status::ok : made.error().code;
    };
    CHECK(refused(entry_of(kind_int, 8), 1, 2) == status::range);
    CHECK(refused(entry_of(kind_int, 8), 4, 0) == status::range);
    CHECK(refused(entry_of(kind_int, 8), 4, max_stream_readers + 1) == status::range);
    CHECK(refused(entry_of(13, 8), 4, 2) == status::range);
    const table_builder big = bytes_array(std::uint64_t{1} << 20);
    CHECK(refused(entry_of(kind_array, 0), std::uint64_t{1} << 30, 2, big.span()) == status::range);
    const auto bad_name = stream::create("bad.name", {}, entry_of(kind_int, 8), 4, 2, 1);
    CHECK(bad_name.error().code == status::range);
}

TEST_CASE("a ring that ends past 4 GiB passes the geometry check") {
    stream_header h{};
    h.capacity = 160;
    h.slot_size = std::uint64_t{32} << 20;
    h.max_readers = 8;
    h.common.waiter_slots = 9;
    detail::stream_shape shape;
    REQUIRE(detail::stream_shape_of(h.capacity, h.slot_size, h.max_readers, 0, shape));
    h.readers = shape.readers;
    h.slots = shape.slots;
    h.types = shape.types;
    CHECK(shape.size > std::uint64_t{4} << 30);
    CHECK(detail::stream_layout::check_geometry(h, shape.size) == status::ok);
}

TEST_CASE("a stream mapping smaller than a page is refused before its header is read") {
    const auto small = std::make_unique<std::byte[]>(64);
    const auto opened = detail::open_header<detail::stream_layout>(small.get(), 64);
    REQUIRE_FALSE(opened);
    CHECK(opened.error().code == status::corrupt);
}
