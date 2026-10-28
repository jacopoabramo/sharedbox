// The metadata area: the CBOR subset check, adding versions, reading them by number and by position, and
// the checks of a forged area.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <initializer_list>
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

// An area in a heap buffer, with the head word that a segment header would hold.
struct area {
    area(std::uint32_t entries, std::uint64_t ring_size)
        : words((detail::metadata_area_size(entries, ring_size) + 7) / 8) {
        REQUIRE(detail::init_metadata(data(), entries, ring_size) == status::ok);
    }
    std::byte *data() { return reinterpret_cast<std::byte *>(words.data()); }
    std::atomic_ref<std::uint64_t> head() { return std::atomic_ref<std::uint64_t>(head_word); }
    metadata_entry &entry(std::uint64_t version) {
        auto *line = reinterpret_cast<metadata_line *>(data());
        return reinterpret_cast<metadata_entry *>(data() + sizeof(metadata_line))[version % line->entries];
    }
    result<std::uint64_t> add(std::span<const std::byte> doc, std::uint64_t position) {
        return detail::add_metadata(data(), head(), doc, position, [](const metadata_entry &) { return true; });
    }
    result<std::vector<std::byte>> read(std::uint64_t version) {
        const result<std::uint32_t> length = detail::metadata_length(data(), version);
        if (!length)
            return sharedbox::unexpected(length.error());
        std::vector<std::byte> out(*length);
        const result<std::uint32_t> got = detail::read_metadata(data(), version, out);
        if (!got)
            return sharedbox::unexpected(got.error());
        out.resize(*got);
        return out;
    }
    std::vector<std::uint64_t> words;
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
        nested(33),
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
    CHECK(code(detail::metadata_version_at(a.data(), 0, 0)) == status::not_found);
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
    CHECK(detail::metadata_version_at(a.data(), head, 4).value_or(0) == 1);
    CHECK(detail::metadata_version_at(a.data(), head, 5).value_or(0) == 2);
    CHECK(detail::metadata_version_at(a.data(), head, 8).value_or(0) == 2);
    CHECK(detail::metadata_version_at(a.data(), head, 9).value_or(0) == 3);
    CHECK(detail::metadata_version_at(a.data(), head, 100).value_or(0) == 3);

    area late(4, 192);
    REQUIRE(late.add(docs[0], 3));
    CHECK(code(detail::metadata_version_at(late.data(), 1, 2)) == status::not_found);
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

    // A third of 200 is 66, rounded down to 64 so that a document wrapping to 0 stops before the newest.
    area b(4, 200);
    CHECK(code(b.add(byte_string(66, 3), 0)) == status::range);
    CHECK(b.add(byte_string(64, 3), 0));
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
    auto record = [&](const metadata_entry &e) {
        asked.push_back(e.version);
        return true;
    };
    for (std::uint8_t v = 1; v <= 4; ++v)
        REQUIRE(detail::add_metadata(a.data(), a.head(), byte_string(64, v), v, record));
    // The fourth document does not fit after the third and starts again at 0, over the first.
    CHECK(asked == std::vector<std::uint64_t>{1});
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
    auto refuse = [&](const metadata_entry &e) {
        asked.push_back(e.version);
        return false;
    };
    for (std::uint8_t v = 1; v <= 2; ++v)
        REQUIRE(detail::add_metadata(a.data(), a.head(), byte_string(64, v), v, refuse));
    // With 8 entries the third fits before the end, so only a fourth reaches the first's bytes.
    if (entries == 8)
        REQUIRE(detail::add_metadata(a.data(), a.head(), byte_string(64, 3), 3, refuse));
    CHECK(asked.empty());
    const std::vector<std::uint64_t> before = a.words;
    const std::uint64_t head = a.head().load();
    const result<std::uint64_t> added = detail::add_metadata(a.data(), a.head(), byte_string(64, 9), 9, refuse);
    REQUIRE_FALSE(added);
    CHECK(code(added) == status::metadata_full);
    CHECK(asked == std::vector<std::uint64_t>{1});
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
    std::atomic<bool> done{false};
    std::uint64_t failed_adds = 0;
    std::thread writer([&] {
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
        const result<std::uint32_t> got = detail::read_metadata(a.data(), v, out);
        if (got) {
            ++reads;
            if (*got != size || out != docs[v % 256])
                ++torn;
        } else if (got.error().code != status::not_found || v + 1 >= a.head().load(std::memory_order_acquire)) {
            ++early_misses;
        }
    };
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
    CHECK(code(detail::read_metadata(a.data(), 1, short_out)) == status::range);

    a.entry(1).offset = 192 - 8;
    CHECK(code(detail::read_metadata(a.data(), 1, std::span<std::byte>(short_out))) == status::corrupt);
    CHECK(code(detail::metadata_length(a.data(), 1)) == status::corrupt);

    reinterpret_cast<metadata_line *>(a.data())->entries = 0;
    CHECK(code(detail::metadata_length(a.data(), 1)) == status::corrupt);
}
