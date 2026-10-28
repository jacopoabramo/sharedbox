// The metadata area: the CBOR subset check, adding versions, reading them by number and by position, and
// the checks of a forged area.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "table_builder.hpp"
#include "unique.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <optional>
#include <span>
#include <string>
#include <thread>
#include <vector>

using namespace sharedbox;
using detail::metadata_entry;
using detail::metadata_line;

namespace {

std::vector<std::byte> cbor(std::initializer_list<int> bytes) {
    std::vector<std::byte> out;
    for (const int b : bytes)
        out.push_back(static_cast<std::byte>(b));
    return out;
}

// A byte string of size bytes in all, 26 to 65538, its payload all value.
std::vector<std::byte> byte_string(std::size_t size, std::uint8_t value) {
    std::vector<std::byte> out(size, static_cast<std::byte>(value));
    if (size <= 257) {
        out[0] = std::byte{0x58};
        out[1] = static_cast<std::byte>(size - 2);
    } else {
        out[0] = std::byte{0x59};
        out[1] = static_cast<std::byte>((size - 3) >> 8);
        out[2] = static_cast<std::byte>((size - 3) & 0xff);
    }
    return out;
}

// count arrays, each holding the next, the innermost empty.
std::vector<std::byte> nested(std::size_t count) {
    std::vector<std::byte> out(count, std::byte{0x81});
    out.back() = std::byte{0x80};
    return out;
}

// The status of r, status::ok for a value, so a check on a refusal fails rather than crashes on a success.
template <class T> status code(const result<T> &r) { return r ? status::ok : r.error().code; }

// count maps, each holding the next under the key "", the innermost empty.
std::vector<std::byte> nested_maps(std::size_t count) {
    std::vector<std::byte> out;
    for (std::size_t i = 1; i < count; ++i) {
        out.push_back(std::byte{0xa1});
        out.push_back(std::byte{0x60});
    }
    out.push_back(std::byte{0xa0});
    return out;
}

// An area in a heap buffer, the checked copy of its first line an open would keep, and the head word that
// a segment header would hold.
struct area {
    area(std::uint32_t entries, std::uint64_t ring_size)
        : words((detail::metadata_area_size(entries, ring_size) + 7) / 8) {
        REQUIRE(detail::init_metadata(data(), entries, ring_size) == status::ok);
        line = detail::copy_metadata_line(data());
        REQUIRE(detail::metadata_geometry_ok(line, static_cast<std::uint32_t>(words.size() * 8)));
    }
    std::byte *data() { return reinterpret_cast<std::byte *>(words.data()); }
    std::atomic_ref<std::uint64_t> head() { return std::atomic_ref<std::uint64_t>(head_word); }
    metadata_entry &entry(std::uint64_t version) {
        return reinterpret_cast<metadata_entry *>(data() + sizeof(metadata_line))[version % line.entries];
    }
    template <class F>
    result<std::uint64_t> add_with(std::span<const std::byte> doc, std::uint64_t position, F &&f) {
        return detail::add_metadata(data(), line, head(), doc, position, f);
    }
    result<std::uint64_t> add(std::span<const std::byte> doc, std::uint64_t position) {
        return add_with(doc, position, [](std::uint64_t) { return true; });
    }
    result<std::uint32_t> length(std::uint64_t version) { return detail::metadata_length(data(), line, version); }
    result<std::uint32_t> read_into(std::uint64_t version, std::span<std::byte> out) {
        return detail::read_metadata(data(), line, version, out);
    }
    result<std::uint64_t> version_at(std::uint64_t at_head, std::uint64_t position) {
        return detail::metadata_version_at(data(), line, at_head, position);
    }
    result<std::vector<std::byte>> read(std::uint64_t version) {
        const result<std::uint32_t> n = length(version);
        if (!n)
            return sharedbox::unexpected(n.error());
        std::vector<std::byte> out(*n);
        const result<std::uint32_t> got = read_into(version, out);
        if (!got)
            return sharedbox::unexpected(got.error());
        out.resize(*got);
        return out;
    }
    std::vector<std::uint64_t> words;
    metadata_line line{};
    std::uint64_t head_word = 0;
};

} // namespace

TEST_CASE("the subset checker accepts each allowed construct") {
    const std::vector<std::vector<std::byte>> accepted = {
        cbor({0xf6}),
        cbor({0xf4}),
        cbor({0xf5}),
        cbor({0x00}),
        cbor({0x20}),
        cbor({0x1b, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff}),
        cbor({0x3b, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff}),
        cbor({0xf9, 0x3c, 0x00}),
        cbor({0xfa, 0x3f, 0x80, 0x00, 0x00}),
        cbor({0xfb, 0x3f, 0xf0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00}),
        cbor({0xf9, 0x7e, 0x00}),
        cbor({0x40}),
        cbor({0x60}),
        cbor({0x64, 0xf0, 0x9f, 0x98, 0x80}),
        cbor({0x80}),
        cbor({0xa0}),
        cbor({0xa2, 0x61, 0x61, 0x01, 0x61, 0x62, 0x02}),
        // Unique keys out of order, which the sorted shortcut does not settle.
        cbor({0xa3, 0x61, 0x63, 0x01, 0x61, 0x61, 0x02, 0x62, 0x61, 0x61, 0x03}),
        nested(32),
        nested_maps(32),
    };
    for (const auto &doc : accepted)
        CHECK(metadata_ok(doc));
}

TEST_CASE("the subset checker refuses the rest") {
    const std::vector<std::vector<std::byte>> refused = {
        cbor({0xc1, 0x00}),
        cbor({0xf7}),
        cbor({0xf0}),
        cbor({0xf8, 0x20}),
        cbor({0x9f, 0xff}),
        cbor({0xff}),
        cbor({0x1c}),
        cbor({0xa1, 0x01, 0x02}),
        cbor({0xa2, 0x61, 0x61, 0x01, 0x61, 0x61, 0x02}),
        // The same key twice with another between them, inside an array.
        cbor({0x81, 0xa3, 0x61, 0x62, 0x01, 0x61, 0x61, 0x02, 0x61, 0x62, 0x03}),
        cbor({0x62, 0xc3, 0x28}),
        cbor({0x62, 0xc0, 0xaf}),
        cbor({0x63, 0xed, 0xa0, 0x80}),
        cbor({0x64, 0xf4, 0x90, 0x80, 0x80}),
        cbor({0xa1, 0x62, 0xc3, 0x28, 0x00}),
        cbor({0x5b, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff}),
        nested(33),
        nested_maps(33),
        cbor({0x62, 0x61}),
        cbor({0x82, 0x00}),
        cbor({0x00, 0x00}),
        {},
    };
    for (const auto &doc : refused)
        CHECK_FALSE(metadata_ok(doc));
}

TEST_CASE("a version added is read back by number and found by position") {
    area a(4, 192);
    CHECK(code(a.version_at(0, 0)) == status::not_found);
    CHECK(code(a.read(1)) == status::not_found);
    const std::vector<std::vector<std::byte>> docs = {
        cbor({0x61, 0x61}),
        cbor({0x83, 0x01, 0x02, 0x03}),
        cbor({0xa1, 0x65, 0x75, 0x6e, 0x69, 0x74, 0x73, 0x62, 0x6d, 0x6d}),
    };
    const std::uint64_t positions[] = {0, 5, 9};
    for (std::uint64_t i = 0; i < 3; ++i) {
        const result<std::uint64_t> v = a.add(docs[i], positions[i]);
        REQUIRE(v);
        CHECK(*v == i + 1);
        CHECK(a.head().load() == i + 1);
    }
    for (std::uint64_t i = 0; i < 3; ++i) {
        const auto got = a.read(i + 1);
        REQUIRE(got);
        CHECK(*got == docs[i]);
    }
    CHECK(code(a.read(0)) == status::not_found);
    CHECK(code(a.read(4)) == status::not_found);
    const std::uint64_t head = a.head().load();
    CHECK(a.version_at(head, 4).value_or(0) == 1);
    CHECK(a.version_at(head, 5).value_or(0) == 2);
    CHECK(a.version_at(head, 8).value_or(0) == 2);
    CHECK(a.version_at(head, 9).value_or(0) == 3);
    CHECK(a.version_at(head, 100).value_or(0) == 3);

    area late(4, 192);
    REQUIRE(late.add(docs[0], 3));
    CHECK(code(late.version_at(1, 2)) == status::not_found);
}

TEST_CASE("a document of a third of the ring is the largest accepted") {
    area a(4, 192);
    const result<std::uint64_t> refused = a.add(byte_string(65, 1), 0);
    REQUIRE_FALSE(refused);
    CHECK(code(refused) == status::range);
    CHECK(a.head().load() == 0);
    CHECK(code(a.add(cbor({0xf7}), 0)) == status::range);
    const result<std::uint64_t> accepted = a.add(byte_string(64, 2), 0);
    REQUIRE(accepted);
    CHECK(*accepted == 1);
    CHECK(a.read(1).value_or(std::vector<std::byte>{}) == byte_string(64, 2));

    // A third of 200 is 66, rounded down to 64, so that a document wrapping to 0 stops before the newest.
    area b(4, 200);
    CHECK(code(b.add(byte_string(65, 3), 0)) == status::range);
    CHECK(b.add(byte_string(64, 3), 0));
}

TEST_CASE("an add never overwrites the newest version, whatever the ring size") {
    // On a ring of 200 a document of 60 then documents of the largest size wrap with the newest past a third
    // of the ring; with a limit of a third unrounded, the wrapping document would reach the newest.
    const std::size_t largest = detail::max_metadata_document(200);
    area a(8, 200);
    REQUIRE(a.add(byte_string(60, 0), 0));
    for (std::uint8_t v = 2; v <= 12; ++v) {
        REQUIRE(a.add(byte_string(largest, v), v));
        const std::vector<std::byte> previous =
            v == 2 ? byte_string(60, 0) : byte_string(largest, static_cast<std::uint8_t>(v - 1));
        CHECK(a.read(v - 1).value_or(std::vector<std::byte>{}) == previous);
        CHECK(a.read(v).value_or(std::vector<std::byte>{}) == byte_string(largest, v));
    }
}

TEST_CASE("a ring below 24 bytes is refused") {
    metadata_line l{};
    l.entries = 4;
    l.ring_size = 16;
    CHECK_FALSE(detail::metadata_geometry_ok(l, UINT32_MAX));
    CHECK(detail::init_metadata(nullptr, 4, 16) == status::range);
    area a(4, 24);
    CHECK(code(a.add(cbor({0x48, 1, 1, 1, 1, 1, 1, 1, 1}), 0)) == status::range);
    CHECK(a.add(cbor({0x47, 1, 1, 1, 1, 1, 1, 1}), 0).value_or(0) == 1);
}

TEST_CASE("wrapping reclaims old versions and keeps the newest") {
    area a(2, 192);
    for (std::uint8_t v = 1; v <= 5; ++v)
        REQUIRE(a.add(byte_string(48, v), v));
    for (std::uint64_t v = 1; v <= 3; ++v)
        CHECK(code(a.read(v)) == status::not_found);
    CHECK(a.read(4).value_or(std::vector<std::byte>{}) == byte_string(48, 4));
    CHECK(a.read(5).value_or(std::vector<std::byte>{}) == byte_string(48, 5));
}

TEST_CASE("a version whose bytes are overwritten is gone though its entry is not reused") {
    area a(8, 192);
    std::vector<std::uint64_t> asked;
    auto record = [&](std::uint64_t next) {
        asked.push_back(next);
        return true;
    };
    for (std::uint8_t v = 1; v <= 4; ++v)
        REQUIRE(a.add_with(byte_string(64, v), 10 * v, record));
    // The fourth document does not fit after the third and starts again at 0, over the first.
    CHECK(asked == std::vector<std::uint64_t>{20});
    CHECK(code(a.read(1)) == status::not_found);
    for (std::uint8_t v = 2; v <= 4; ++v)
        CHECK(a.read(v).value_or(std::vector<std::byte>{}) == byte_string(64, v));
}

TEST_CASE("a refused reclaim writes nothing") {
    std::uint32_t entries = 0;
    SUBCASE("the entry is reused") { entries = 2; }
    SUBCASE("the bytes are overwritten") { entries = 8; }
    area a(entries, 192);
    std::vector<std::uint64_t> asked;
    auto refuse = [&](std::uint64_t next) {
        asked.push_back(next);
        return false;
    };
    for (std::uint8_t v = 1; v <= 2; ++v)
        REQUIRE(a.add_with(byte_string(64, v), 10 * v, refuse));
    // With 8 entries the third fits before the end, so only a fourth reaches the first's bytes.
    if (entries == 8)
        REQUIRE(a.add_with(byte_string(64, 3), 30, refuse));
    CHECK(asked.empty());
    const std::vector<std::uint64_t> before = a.words;
    const std::uint64_t head = a.head().load();
    const result<std::uint64_t> added = a.add_with(byte_string(64, 9), 90, refuse);
    REQUIRE_FALSE(added);
    CHECK(code(added) == status::metadata_full);
    CHECK(asked == std::vector<std::uint64_t>{20});
    CHECK(a.head().load() == head);
    CHECK(a.words == before);
}

TEST_CASE("a read during adds is never torn") {
    // Each document is a third of the ring, so version v is overwritten by the add of v + 3 and a read of
    // the head's version can fail only once the head has moved on by at least 2. Documents this long keep
    // each copy long enough for an add to overlap it.
    constexpr std::size_t size = 4096;
    area a(4, 3 * size);
    std::vector<std::vector<std::byte>> docs;
    for (int value = 0; value < 256; ++value)
        docs.push_back(byte_string(size, static_cast<std::uint8_t>(value)));
    constexpr std::uint64_t adds = 10000;
    std::atomic<bool> started{false};
    std::atomic<bool> done{false};
    std::uint64_t failed_adds = 0;
    std::thread writer([&] {
        while (!started.load())
            std::this_thread::yield();
        for (std::uint64_t v = 1; v <= adds; ++v)
            if (!a.add(docs[v % 256], v))
                ++failed_adds;
        done = true;
    });
    std::uint64_t torn = 0;
    std::uint64_t early_misses = 0;
    std::uint64_t reads = 0;
    std::vector<std::byte> out(size);
    const auto read = [&](std::uint64_t v) {
        const result<std::uint32_t> got = a.read_into(v, out);
        if (got) {
            ++reads;
            if (*got != size || out != docs[v % 256])
                ++torn;
        } else if (got.error().code != status::not_found || v + 1 >= a.head().load(std::memory_order_acquire)) {
            ++early_misses;
        }
    };
    started = true;
    while (!done.load()) {
        const std::uint64_t h = a.head().load(std::memory_order_acquire);
        if (h == 0)
            continue;
        read(h);
        // The add of h + 1 overwrites version h - 2, so this read races the writer most of the time.
        if (h > 2)
            read(h - 2);
    }
    writer.join();
    CHECK(failed_adds == 0);
    CHECK(torn == 0);
    CHECK(early_misses == 0);
    CHECK(a.head().load() == adds);
    CHECK(reads > 0);
}

TEST_CASE("a forged area is refused") {
    const auto line = [](std::uint32_t entries, std::uint64_t ring_size) {
        metadata_line l{};
        l.entries = entries;
        l.ring_size = ring_size;
        return l;
    };
    const auto size = static_cast<std::uint32_t>(detail::metadata_area_size(4, 192));
    CHECK(detail::metadata_geometry_ok(line(4, 192), size));
    CHECK_FALSE(detail::metadata_geometry_ok(line(1, 192), size));
    CHECK_FALSE(detail::metadata_geometry_ok(line(65536, 192), UINT32_MAX));
    CHECK_FALSE(detail::metadata_geometry_ok(line(4, 188), size));
    CHECK_FALSE(detail::metadata_geometry_ok(line(4, 192), size - 1));
    CHECK(detail::init_metadata(nullptr, 1, 192) == status::range);
    CHECK(detail::init_metadata(nullptr, 4, 100) == status::range);

    area a(4, 192);
    const std::vector<std::byte> doc = byte_string(30, 7);
    REQUIRE(a.add(doc, 0));
    std::vector<std::byte> short_out(doc.size() - 1);
    CHECK(code(a.read_into(1, short_out)) == status::range);

    a.entry(1).offset = 192 - 8;
    CHECK(code(a.read_into(1, std::span<std::byte>(short_out))) == status::corrupt);
    CHECK(code(a.length(1)) == status::corrupt);

    // A ring size rewritten in the area after the open would place the document inside; the checked copy
    // does not.
    reinterpret_cast<metadata_line *>(a.data())->ring_size = std::uint64_t{1} << 40;
    CHECK(code(a.length(1)) == status::corrupt);
    CHECK(code(a.read_into(1, std::span<std::byte>(short_out))) == status::corrupt);
    CHECK(code(a.add(byte_string(30, 8), 1)) == status::corrupt);
}

TEST_CASE("a forged head is refused without a walk over every version") {
    area a(4, 192);
    REQUIRE(a.add(byte_string(30, 1), 0));
    // For UINT64_MAX, 2 * version + 2 wraps to 0, the seq of an entry whose bytes were overwritten.
    a.entry(UINT64_MAX).version = UINT64_MAX;
    a.entry(UINT64_MAX).length = 30;
    const std::vector<std::uint64_t> before = a.words;
    a.head().store(UINT64_MAX);
    CHECK(code(a.add(byte_string(30, 2), 1)) == status::corrupt);
    CHECK(code(a.read(UINT64_MAX)) == status::not_found);
    CHECK(code(a.version_at(UINT64_MAX, 0)) == status::not_found);
    CHECK(a.words == before);

    // The head before the newest version an add can make, with an entry that holds it: the add still makes
    // that version, and the add after it, whose seq would wrap to 0, is refused.
    const std::uint64_t last = detail::metadata_version_limit - 1;
    metadata_entry &e = a.entry(last - 1);
    e = a.entry(1);
    e.seq = 2 * (last - 1) + 2;
    e.version = last - 1;
    a.head().store(last - 1);
    CHECK(a.read(last - 1).value_or(std::vector<std::byte>{}) == byte_string(30, 1));
    CHECK(a.add(byte_string(30, 2), 1).value_or(0) == last);
    CHECK(a.read(last).value_or(std::vector<std::byte>{}) == byte_string(30, 2));
    const std::vector<std::uint64_t> forged = a.words;
    CHECK(code(a.add(byte_string(30, 3), 2)) == status::corrupt);
    CHECK(a.words == forged);
    CHECK(a.head().load() == last);
}

TEST_CASE("a reclaim is decided by the position of the version after the one reclaimed") {
    // A stream of 8 slots whose sender is at write_pos: version u may go once no item in the ring was sent
    // under it, that is once version u + 1 took effect at or before write_pos - 8.
    area a(2, 192);
    std::uint64_t write_pos = 0;
    const auto stream_rule = [&](std::uint64_t next) { return next + 8 <= write_pos; };
    REQUIRE(a.add_with(cbor({0x01}), 0, stream_rule));
    REQUIRE(a.add_with(cbor({0x02}), 4, stream_rule));
    write_pos = 10;
    const result<std::uint64_t> early = a.add_with(cbor({0x03}), write_pos, stream_rule);
    REQUIRE_FALSE(early);
    CHECK(code(early) == status::metadata_full);
    write_pos = 12;
    CHECK(a.add_with(cbor({0x03}), write_pos, stream_rule).value_or(0) == 3);
    CHECK(code(a.read(1)) == status::not_found);
    CHECK(a.version_at(a.head().load(), 11).value_or(0) == 2);
}

namespace {

constexpr field_spec box_fields[2] = {{0, 8, kind_int}, {8, 16, kind_bytes}};

std::byte *area_of(const handle &h) {
    return static_cast<std::byte *>(h.base()) + static_cast<header *>(h.base())->metadata;
}

std::vector<std::byte> read_version(const handle &h, std::uint64_t version) {
    std::vector<std::byte> out(256);
    const auto got = h.read_metadata(version, out);
    REQUIRE(got);
    out.resize(*got);
    return out;
}

status open_status(const std::string &name) {
    const auto opened = handle::open(name, seconds(0.2));
    return opened ? status::ok : opened.error().code;
}

} // namespace

TEST_CASE("a box created with metadata has version 1 at open") {
    const std::string name = unique("meta-open");
    const auto doc = cbor({0xa1, 0x61, 0x61, 0x01});
    auto owner = handle::create(name, box_fields, 32, 1, 4, {}, {}, metadata_spec{doc, 4096, 8});
    REQUIRE(owner);
    auto other = handle::open(name, seconds(1.0));
    REQUIRE(other);
    CHECK(other->metadata_head() == 1);
    CHECK(read_version(*other, 1) == doc);
    CHECK(other->metadata_length(1).value_or(0) == doc.size());
    CHECK(code(other->metadata_length(2)) == status::not_found);
    CHECK(owner->generation() == 0);
    static_cast<void>(unlink(name));
}

TEST_CASE("a metadata area with no initial document starts at head 0") {
    const std::string name = unique("meta-empty");
    auto owner = handle::create(name, box_fields, 32, 1, 4, {}, {}, metadata_spec{{}, 1024, 4});
    REQUIRE(owner);
    CHECK(owner->metadata_head() == 0);
    CHECK(code(owner->metadata_length(1)) == status::not_found);
    CHECK(owner->add_metadata(cbor({0x01}), seconds(1.0)).value_or(0) == 1);
    static_cast<void>(unlink(name));
}

TEST_CASE("create refuses a metadata spec that cannot be laid out") {
    const std::string name = unique("meta-spec");
    const auto doc = cbor({0x01});
    const auto made = [&](const metadata_spec &spec) {
        auto h = handle::create(name, box_fields, 32, 1, 4, {}, {}, spec);
        const status rc = h ? status::ok : h.error().code;
        static_cast<void>(unlink(name));
        return rc;
    };
    CHECK(made({doc, 0, 8}) == status::range);
    CHECK(made({{}, 4096, 1}) == status::range);
    CHECK(made({{}, 4096, 65536}) == status::range);
    CHECK(made({{}, 16, 8}) == status::range);
    CHECK(made({{}, std::uint64_t{1} << 33, 8}) == status::range);
    CHECK(made({cbor({0xc0, 0x01}), 4096, 8}) == status::range);
    CHECK(made({byte_string(300, 1), 600, 8}) == status::range);
    CHECK(made({doc, 24, 2}) == status::ok);
    CHECK(made({{}, 0, 0}) == status::ok);
    CHECK(open_status(name) == status::not_found);
}

TEST_CASE("adding box metadata is one write") {
    const std::string name = unique("meta-add");
    auto owner = handle::create(name, box_fields, 32, 1, 4, {}, {}, metadata_spec{cbor({0x01}), 4096, 8});
    REQUIRE(owner);
    const std::uint8_t number[8] = {7};
    const value v{0, std::as_bytes(std::span(number))};
    REQUIRE(owner->write({&v, 1}, seconds(1.0)));
    std::byte before[8], after[8];
    const auto first = owner->read(0, before);
    REQUIRE(first);
    const std::uint64_t generation = owner->generation();
    const auto slot = owner->register_waiter();
    REQUIRE(slot);
    std::optional<result<wake>> woke;
    std::thread waiter([&] { woke = owner->wait(*slot, generation, seconds(5.0)); });
    while (owner->sleepers() == 0)
        std::this_thread::yield();
    const auto added = owner->add_metadata(cbor({0x02}), seconds(1.0));
    waiter.join();
    REQUIRE(added);
    CHECK(*added == 2);
    CHECK(owner->generation() == generation + 1);
    CHECK((woke && *woke && **woke == wake::changed));
    const auto second = owner->read(0, after);
    REQUIRE(second);
    CHECK(second->version == first->version);
    CHECK(std::memcmp(before, after, 8) == 0);
    CHECK(owner->version(0) == first->version);
    CHECK(owner->version(1) == 0);
    CHECK(owner->writer_pid() == 0);
    const metadata_line line = detail::copy_metadata_line(area_of(*owner));
    CHECK(detail::metadata_version_at(area_of(*owner), line, 2, owner->generation()).value_or(0) == 2);
    CHECK(detail::metadata_version_at(area_of(*owner), line, 2, owner->generation() - 1).value_or(0) == 1);
    CHECK(read_version(*owner, 2) == cbor({0x02}));
    owner->release_waiter(*slot);
    static_cast<void>(unlink(name));
}

TEST_CASE("a refused add leaves the generation alone") {
    const std::string name = unique("meta-refused");
    auto owner = handle::create(name, box_fields, 32, 1, 4, {}, {}, metadata_spec{cbor({0x01}), 1024, 4});
    REQUIRE(owner);
    const std::uint64_t generation = owner->generation();
    CHECK(code(owner->add_metadata(cbor({0xc0, 0x01}), seconds(1.0))) == status::range);
    CHECK(owner->generation() == generation);
    CHECK(owner->writer_pid() == 0);
    CHECK(owner->add_metadata(cbor({0x02}), seconds(1.0)).value_or(0) == 2);
    CHECK(owner->generation() == generation + 1);
    static_cast<void>(unlink(name));
}

TEST_CASE("a reader of the latest box version never misses it during an add") {
    const std::string name = unique("meta-race");
    constexpr std::uint64_t total = 2000;
    auto owner = handle::create(name, box_fields, 32, 1, 4, {}, {}, metadata_spec{{}, 256, 16});
    REQUIRE(owner);
    auto reader = handle::open(name, seconds(1.0));
    REQUIRE(reader);
    std::atomic<std::uint64_t> seen{0};
    std::atomic<bool> stop{false};
    std::atomic<std::uint64_t> misses{0};
    std::atomic<std::uint64_t> wrong{0};
    std::thread read([&] {
        std::byte buf[16];
        for (std::uint64_t head = 0; head < total && !stop.load();) {
            head = reader->metadata_head();
            if (head == 0)
                continue;
            const auto got = reader->read_metadata(head, buf);
            if (!got) {
                misses.fetch_add(1);
                continue;
            }
            const std::uint64_t number =
                std::to_integer<std::uint64_t>(buf[1]) << 8 | std::to_integer<std::uint64_t>(buf[2]);
            if (*got != 3 || buf[0] != std::byte{0x19} || number != head)
                wrong.fetch_add(1);
            seen.store(head);
        }
    });
    for (std::uint64_t v = 1; v <= total; ++v) {
        // The reader stays within a few versions of the writer, so only an add that hides the newest
        // version can make it miss.
        while (v > seen.load() + 4 && seen.load() < total)
            std::this_thread::yield();
        const auto doc = cbor({0x19, static_cast<int>(v >> 8), static_cast<int>(v & 0xff)});
        const bool added = owner->add_metadata(doc, seconds(5.0)).value_or(0) == v;
        CHECK(added);
        if (!added)
            break;
    }
    stop.store(true);
    read.join();
    CHECK(misses.load() == 0);
    CHECK(wrong.load() == 0);
    CHECK(owner->generation() == total);
    static_cast<void>(unlink(name));
}

TEST_CASE("a forged box metadata offset is refused") {
    const std::string name = unique("meta-forged");
    auto owner = handle::create(name, box_fields, 32, 1, 4, {}, {}, metadata_spec{cbor({0x01}), 1024, 4});
    REQUIRE(owner);
    header &h = *static_cast<header *>(owner->base());
    const auto forged = [&](auto &field, auto value) {
        const auto saved = field;
        field = value;
        const status rc = open_status(name);
        field = saved;
        return rc;
    };
    const std::uint64_t area = h.metadata;
    const std::uint32_t used = h.metadata_size;
    CHECK(open_status(name) == status::ok);
    CHECK(area % 64 == 0);
    CHECK(area >= h.record + h.record_size);
    CHECK(forged(h.metadata, h.common.size) == status::corrupt);
    CHECK(forged(h.metadata, area + 4096) == status::corrupt);
    CHECK(forged(h.metadata, std::uint64_t{h.record}) == status::corrupt);
    CHECK(forged(h.metadata, area + 64) == status::corrupt);
    CHECK(forged(h.metadata, area + 8) == status::corrupt);
    CHECK(forged(h.metadata_size, used + 4096) == status::corrupt);
    CHECK(forged(h.metadata_size, std::uint32_t{32}) == status::corrupt);
    CHECK(forged(h.metadata_size, used - 8) == status::corrupt);
    CHECK(forged(h.metadata, std::uint64_t{0}) == status::corrupt);
    h.metadata = 0;
    h.metadata_size = 0;
    CHECK(open_status(name) == status::ok);
    h.metadata = area;
    h.metadata_size = used;
    auto *line = reinterpret_cast<metadata_line *>(area_of(*owner));
    const std::uint32_t entries = line->entries;
    line->entries = 1;
    CHECK(open_status(name) == status::corrupt);
    line->entries = entries;
    line->ring_size += 8;
    CHECK(open_status(name) == status::corrupt);
    line->ring_size -= 8;
    CHECK(open_status(name) == status::ok);
    static_cast<void>(unlink(name));
}

TEST_CASE("metadata is read and added through a duplicate and a taken-over capsule") {
    const std::string name = unique("meta-dup");
    const auto doc = cbor({0xa1, 0x61, 0x61, 0x01});
    auto owner = handle::create(name, box_fields, 32, 1, 4, {}, {}, metadata_spec{doc, 1024, 4});
    REQUIRE(owner);
    auto copy = owner->duplicate();
    REQUIRE(copy);
    CHECK(copy->metadata_head() == 1);
    CHECK(read_version(*copy, 1) == doc);
    CHECK(copy->add_metadata(cbor({0x02}), seconds(1.0)).value_or(0) == 2);
    sbx_handle *capsule = std::move(*copy).to_capsule();
    REQUIRE(capsule != nullptr);
    auto taken = handle::from_capsule(capsule);
    REQUIRE(taken);
    CHECK(taken->metadata_head() == 2);
    CHECK(read_version(*taken, 2) == cbor({0x02}));
    CHECK(taken->add_metadata(cbor({0x03}), seconds(1.0)).value_or(0) == 3);
    CHECK(owner->metadata_head() == 3);
    CHECK(read_version(*owner, 3) == cbor({0x03}));
    delete capsule;
    static_cast<void>(unlink(name));
}

namespace {

stream make_stream(const std::string &name, std::uint64_t capacity, const metadata_spec &spec) {
    auto made = stream::create(name, {}, entry_of(kind_int, 8), capacity, 2, 1, spec);
    REQUIRE(made);
    return std::move(*made);
}

result<std::uint64_t> send_item(stream_sender &sender, std::uint64_t value) {
    return sender.send(std::as_bytes(std::span{&value, 1}), seconds(1.0));
}

void send_items(stream_sender &sender, std::uint64_t count) {
    for (std::uint64_t i = 0; i < count; ++i)
        REQUIRE(send_item(sender, i));
}

std::vector<std::byte> read_stream_version(const stream &s, std::uint64_t version) {
    std::vector<std::byte> out(256);
    const auto got = s.read_metadata(version, out);
    REQUIRE(got);
    out.resize(*got);
    return out;
}

status stream_open_status(const std::string &name) {
    const auto opened = stream::open(name, seconds(0.2));
    return opened ? status::ok : opened.error().code;
}

} // namespace

TEST_CASE("a stream version applies from the next item") {
    const std::string name = unique("meta-stream-next");
    stream made = make_stream(name, 8, metadata_spec{cbor({0x01}), 1024, 4});
    auto other = stream::open(name, seconds(1.0));
    REQUIRE(other);
    CHECK(other->metadata_head() == 1);
    CHECK(read_stream_version(*other, 1) == cbor({0x01}));
    auto sender = made.sender();
    auto reader = made.reader(read_mode::lossy, start_at::oldest);
    REQUIRE(sender);
    REQUIRE(reader);
    send_items(*sender, 3);
    CHECK(sender->add_metadata(cbor({0x02})).value_or(0) == 2);
    send_items(*sender, 2);
    CHECK(other->metadata_head() == 2);
    CHECK(read_stream_version(*other, 2) == cbor({0x02}));
    for (const std::uint64_t position : {0, 1, 2})
        CHECK(reader->metadata_version_at(position).value_or(0) == 1);
    for (const std::uint64_t position : {3, 4})
        CHECK(reader->metadata_version_at(position).value_or(0) == 2);
    static_cast<void>(unlink(name));
}

TEST_CASE("a stream refuses to reclaim a version an item in the ring needs") {
    const std::string name = unique("meta-stream-reclaim");
    stream made = make_stream(name, 8, metadata_spec{cbor({0x01}), 1024, 2});
    auto sender = made.sender();
    REQUIRE(sender);
    send_items(*sender, 4);
    REQUIRE(sender->add_metadata(cbor({0x02})).value_or(0) == 2);
    send_items(*sender, 6);
    CHECK(code(sender->add_metadata(cbor({0x03}))) == status::metadata_full);
    CHECK(made.metadata_head() == 2);
    send_items(*sender, 2);
    CHECK(sender->add_metadata(cbor({0x03})).value_or(0) == 3);
    static_cast<void>(unlink(name));
}

TEST_CASE("a lossy reader whose item version was reclaimed is told") {
    const std::string name = unique("meta-stream-lossy");
    stream made = make_stream(name, 4, metadata_spec{cbor({0x01}), 1024, 2});
    auto sender = made.sender();
    REQUIRE(sender);
    send_items(*sender, 3);
    auto reader = made.reader(read_mode::lossy, start_at::oldest);
    REQUIRE(reader);
    std::uint64_t last = 0, value = 0;
    for (int i = 0; i < 3; ++i) {
        const auto got = reader->receive(std::as_writable_bytes(std::span{&value, 1}), seconds(1.0));
        REQUIRE(got);
        last = got->position;
    }
    CHECK(last == 2);
    REQUIRE(sender->add_metadata(cbor({0x02})).value_or(0) == 2);
    CHECK(reader->metadata_version_at(last).value_or(0) == 1);
    send_items(*sender, 4);
    CHECK(sender->add_metadata(cbor({0x03})).value_or(0) == 3);
    CHECK(code(reader->metadata_version_at(last)) == status::not_found);
    CHECK(reader->metadata_version_at(7).value_or(0) == 3);
    static_cast<void>(unlink(name));
}

TEST_CASE("a stream without a metadata area has no versions") {
    const std::string name = unique("meta-stream-none");
    stream made = make_stream(name, 4, {});
    auto sender = made.sender();
    auto reader = made.reader(read_mode::lossy, start_at::newest);
    REQUIRE(sender);
    REQUIRE(reader);
    CHECK(made.metadata_head() == 0);
    CHECK(code(made.metadata_length(1)) == status::not_found);
    CHECK(code(sender->add_metadata(cbor({0x01}))) == status::metadata_full);
    CHECK(code(reader->metadata_version_at(0)) == status::not_found);
    static_cast<void>(unlink(name));
}

TEST_CASE("a stream refuses a bad document and reports a closed sender") {
    const std::string name = unique("meta-stream-bad");
    stream made = make_stream(name, 4, metadata_spec{{}, 120, 2});
    auto sender = made.sender();
    REQUIRE(sender);
    CHECK(made.metadata_head() == 0);
    CHECK(code(sender->add_metadata(cbor({0xc0, 0x01}))) == status::range);
    CHECK(code(sender->add_metadata(byte_string(60, 0x01))) == status::range);
    CHECK(made.metadata_head() == 0);
    sender->close();
    CHECK(code(sender->add_metadata(cbor({0x01}))) == code(send_item(*sender, 0)));
    static_cast<void>(unlink(name));
}

TEST_CASE("create refuses a stream metadata spec that cannot be laid out") {
    const std::string name = unique("meta-stream-spec");
    const auto refused = [&](const metadata_spec &spec) {
        const auto made = stream::create(name, {}, entry_of(kind_int, 8), 4, 2, 1, spec);
        return made ? status::ok : made.error().code;
    };
    CHECK(refused(metadata_spec{cbor({0x01}), 0, 4}) == status::range);
    CHECK(refused(metadata_spec{{}, 1024, 1}) == status::range);
    CHECK(refused(metadata_spec{{}, 16, 4}) == status::range);
    CHECK(refused(metadata_spec{cbor({0xc0, 0x01}), 1024, 4}) == status::range);
    CHECK(stream_open_status(name) == status::not_found);
}

TEST_CASE("a forged stream metadata offset is refused") {
    const std::string name = unique("meta-stream-forged");
    stream made = make_stream(name, 4, metadata_spec{cbor({0x01}), 1024, 4});
    stream_header &h = *static_cast<stream_header *>(made.base());
    const auto forged = [&](auto &field, auto value) {
        const auto saved = field;
        field = value;
        const status rc = stream_open_status(name);
        field = saved;
        return rc;
    };
    const std::uint64_t area = h.metadata;
    const std::uint32_t used = h.metadata_size;
    CHECK(stream_open_status(name) == status::ok);
    CHECK(area % 64 == 0);
    CHECK(area >= h.types + h.types_size);
    CHECK(forged(h.metadata, h.common.size) == status::corrupt);
    CHECK(forged(h.metadata, area + 4096) == status::corrupt);
    CHECK(forged(h.metadata, h.types - 64) == status::corrupt);
    CHECK(forged(h.metadata, area + 64) == status::corrupt);
    CHECK(forged(h.metadata, area + 8) == status::corrupt);
    CHECK(forged(h.metadata, UINT64_MAX - 63) == status::corrupt);
    CHECK(forged(h.metadata_size, used + 4096) == status::corrupt);
    CHECK(forged(h.metadata_size, std::uint32_t{32}) == status::corrupt);
    CHECK(forged(h.metadata_size, used - 8) == status::corrupt);
    CHECK(forged(h.metadata_size, std::uint32_t{0}) == status::corrupt);
    CHECK(forged(h.metadata, std::uint64_t{0}) == status::corrupt);
    auto *line = reinterpret_cast<metadata_line *>(static_cast<std::byte *>(made.base()) + area);
    const std::uint32_t entries = line->entries;
    line->entries = 1;
    CHECK(stream_open_status(name) == status::corrupt);
    line->entries = entries;
    line->ring_size += 8;
    CHECK(stream_open_status(name) == status::corrupt);
    line->ring_size -= 8;
    CHECK(stream_open_status(name) == status::ok);
    static_cast<void>(unlink(name));
}

TEST_CASE("a version that covers no item is reclaimed without waiting") {
    const std::string name = unique("meta-stream-empty-version");
    stream made = make_stream(name, 8, metadata_spec{cbor({0x01}), 1024, 2});
    auto sender = made.sender();
    auto reader = made.reader(read_mode::lossy, start_at::newest);
    REQUIRE(sender);
    REQUIRE(reader);
    CHECK(sender->add_metadata(cbor({0x02})).value_or(0) == 2);
    CHECK(sender->add_metadata(cbor({0x03})).value_or(0) == 3);
    CHECK(sender->add_metadata(cbor({0x04})).value_or(0) == 4);
    CHECK(reader->metadata_version_at(0).value_or(0) == 4);
    send_items(*sender, 3);
    CHECK(sender->add_metadata(cbor({0x05})).value_or(0) == 5);
    CHECK(code(sender->add_metadata(cbor({0x06}))) == status::metadata_full);
    for (const std::uint64_t position : {0, 1, 2})
        CHECK(reader->metadata_version_at(position).value_or(0) == 4);
    CHECK(reader->metadata_version_at(3).value_or(0) == 5);
    send_items(*sender, 8);
    CHECK(sender->add_metadata(cbor({0x06})).value_or(0) == 6);
    CHECK(reader->metadata_version_at(11).value_or(0) == 6);
    CHECK(reader->metadata_version_at(3).value_or(0) == 5);
    static_cast<void>(unlink(name));
}

TEST_CASE("version 1 is kept while an item sent before it is in the ring") {
    const std::string name = unique("meta-stream-before-first");
    stream made = make_stream(name, 8, metadata_spec{{}, 1024, 2});
    auto sender = made.sender();
    auto reader = made.reader(read_mode::lossless, start_at::oldest);
    REQUIRE(sender);
    REQUIRE(reader);
    send_items(*sender, 2);
    REQUIRE(sender->add_metadata(cbor({0x01})).value_or(0) == 1);
    REQUIRE(sender->add_metadata(cbor({0x02})).value_or(0) == 2);
    CHECK(code(sender->add_metadata(cbor({0x03}))) == status::metadata_full);
    CHECK(made.metadata_length(1).value_or(0) == 1);
    CHECK(code(reader->metadata_version_at(0)) == status::not_found);
    CHECK(reader->metadata_version_at(2).value_or(0) == 2);
    std::uint64_t value = 0;
    for (int i = 0; i < 2; ++i)
        REQUIRE(reader->receive(std::as_writable_bytes(std::span{&value, 1}), seconds(1.0)));
    send_items(*sender, 8);
    CHECK(sender->add_metadata(cbor({0x03})).value_or(0) == 3);
    CHECK(code(made.metadata_length(1)) == status::not_found);
    static_cast<void>(unlink(name));
}

TEST_CASE("a stream refuses to overwrite the bytes of a version an item needs") {
    const std::string name = unique("meta-stream-bytes");
    stream made = make_stream(name, 8, metadata_spec{byte_string(26, 1), 96, 64});
    auto sender = made.sender();
    REQUIRE(sender);
    send_items(*sender, 3);
    REQUIRE(sender->add_metadata(byte_string(26, 2)).value_or(0) == 2);
    send_items(*sender, 3);
    REQUIRE(sender->add_metadata(byte_string(26, 3)).value_or(0) == 3);
    CHECK(code(sender->add_metadata(byte_string(26, 4))) == status::metadata_full);
    CHECK(made.metadata_head() == 3);
    send_items(*sender, 5);
    CHECK(sender->add_metadata(byte_string(26, 4)).value_or(0) == 4);
    CHECK(code(made.metadata_length(1)) == status::not_found);
    CHECK(read_stream_version(made, 2) == byte_string(26, 2));
    static_cast<void>(unlink(name));
}

TEST_CASE("a stream mapping larger than its area needs opens") {
    const std::string name = unique("meta-stream-larger");
    const auto geometry = [&](const metadata_spec &spec, std::uint64_t extra) {
        stream made = make_stream(name, 4, spec);
        const stream_header &h = *static_cast<const stream_header *>(made.base());
        const stream_header copy = detail::stream_layout::copy_header(&h);
        const status rc = detail::stream_layout::check_geometry(copy, h.common.size + extra);
        static_cast<void>(unlink(name));
        return rc;
    };
    CHECK(geometry(metadata_spec{cbor({0x01}), 1024, 4}, 0) == status::ok);
    CHECK(geometry(metadata_spec{cbor({0x01}), 1024, 4}, 4096) == status::ok);
    CHECK(geometry(metadata_spec{}, 4096) == status::ok);
}

TEST_CASE("versions with no items are reclaimed in age order only") {
    const std::string name = unique("meta-stream-age-order");
    const auto short_bytes = [](std::size_t total) {
        std::vector<std::byte> out(total, std::byte{0x07});
        out[0] = static_cast<std::byte>(0x40 + total - 1);
        return out;
    };
    stream made = make_stream(name, 8, metadata_spec{byte_string(32, 1), 96, 64});
    auto sender = made.sender();
    auto reader = made.reader(read_mode::lossy, start_at::newest);
    REQUIRE(sender);
    REQUIRE(reader);
    REQUIRE(sender->add_metadata(byte_string(32, 2)).value_or(0) == 2);
    REQUIRE(sender->add_metadata(short_bytes(16)).value_or(0) == 3);
    REQUIRE(sender->add_metadata(short_bytes(8)).value_or(0) == 4);
    send_items(*sender, 3);
    REQUIRE(sender->add_metadata(short_bytes(16)).value_or(0) == 5);
    REQUIRE(sender->add_metadata(byte_string(32, 3)).value_or(0) == 6);
    REQUIRE(sender->add_metadata(byte_string(32, 4)).value_or(0) == 7);
    CHECK(code(sender->add_metadata(byte_string(32, 5))) == status::metadata_full);
    for (const std::uint64_t position : {0, 1, 2})
        CHECK(reader->metadata_version_at(position).value_or(0) == 4);
    static_cast<void>(unlink(name));
}
