// Seeded mutations of valid description tables, metadata documents and box segments, so that the parsers of
// bytes another process wrote run on damaged input under the sanitizers.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "table_builder.hpp"
#include "unique.hpp"

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <memory>
#include <random>
#include <span>
#include <string>
#include <utility>
#include <vector>

using namespace sharedbox;
using sharedbox::detail::type_table;

namespace {

// The sanitizer builds of CI pass their own count.
#ifdef SHAREDBOX_TEST_ROUNDS
constexpr int rounds = SHAREDBOX_TEST_ROUNDS;
#else
constexpr int rounds = 5000;
#endif

// A fixed seed, so a failure shows again on the next run.
constexpr std::uint64_t seed = 0x5EED;

std::byte random_byte(std::mt19937_64 &rng) { return static_cast<std::byte>(rng() & 0xFF); }

// Applies one to four changes: a flipped bit, a random byte, 8 random bytes inserted, or the end cut off.
// With whole_words, inserts and cuts keep the size a multiple of 8, so a table passes the size check and
// reaches the parser.
void mutate(std::vector<std::byte> &bytes, std::mt19937_64 &rng, bool whole_words) {
    const int changes = 1 + static_cast<int>(rng() % 4);
    for (int i = 0; i < changes && !bytes.empty(); ++i) {
        const std::size_t at = rng() % bytes.size();
        switch (rng() % 4) {
        case 0:
            bytes[at] ^= static_cast<std::byte>(1u << (rng() % 8));
            break;
        case 1:
            bytes[at] = random_byte(rng);
            break;
        case 2: {
            const std::size_t where = whole_words ? at - at % 8 : at;
            for (int k = 0; k < 8; ++k)
                bytes.insert(bytes.begin() + static_cast<std::ptrdiff_t>(where), random_byte(rng));
            break;
        }
        default: {
            const std::size_t keep = whole_words ? at - at % 8 : at;
            bytes.resize(keep);
        }
        }
    }
}

struct table_seed {
    table_builder table;
    std::uint32_t entry;
    bool max_arrays = false;
};

table_builder colour() {
    table_builder t;
    t.head(kind_enum, 2, 2);
    t.name("RED");
    t.name("GREEN");
    t.pad();
    return t;
}

// A list of at most 3 ints at offset 0: the count, padded to 8, then the elements.
table_builder int_list() {
    table_builder t;
    t.head(kind_list, 1, 32);
    t.put(std::uint32_t{3});
    t.entry(8, kind_int, 8);
    t.pad();
    return t;
}

// record { a: int, b: list[int] of 3 }: the record at 0, its list after it.
table_builder record_with_list() {
    table_builder t;
    t.head(kind_record, 2, 40);
    t.entry(0, kind_int, 8);
    const std::size_t list_entry = t.bytes.size();
    t.entry(8, kind_list, 0);
    t.name("a");
    t.name("b");
    t.pad();
    const std::uint32_t list = t.head(kind_list, 1, 32);
    t.put(std::uint32_t{3});
    t.entry(8, kind_int, 8);
    t.pad();
    t.patch(list_entry + 4, entry_of(kind_list, list));
    return t;
}

table_builder optional_int() {
    table_builder t;
    t.head(kind_optional, 1, 16);
    t.entry(8, kind_int, 8);
    t.pad();
    return t;
}

table_builder lone_max_array() {
    table_builder t;
    max_array(t, {4, 4});
    return t;
}

std::vector<table_seed> table_seeds() {
    std::vector<table_seed> seeds;
    seeds.push_back({colour(), entry_of(kind_enum, 0)});
    seeds.push_back({int_list(), entry_of(kind_list, 0)});
    seeds.push_back({record_with_list(), entry_of(kind_record, 0)});
    seeds.push_back({optional_int(), entry_of(kind_optional, 0)});
    seeds.push_back({lone_max_array(), entry_of(kind_max_array, 0), true});
    seeds.push_back({around_max_array(kind_record), entry_of(kind_record, 0), true});
    seeds.push_back({around_max_array(kind_tuple), entry_of(kind_tuple, 0), true});
    seeds.push_back({timed(kind_float, 8), entry_of(kind_timed, 0)});
    seeds.push_back({attr({{kind_int, 8}}, {{kind_int, 8}}, attr_timed | attr_settable | attr_request),
                     entry_of(kind_attr, 0), true});
    return seeds;
}

// CBOR documents metadata_ok accepts: {"a": 1, "b": [1.5, "x"], "c": {"d": true}},
// {"unit": "mm", "range": [1.0 as a half float, -10]} and {"n": [[[[[0]]]]]}.
std::vector<std::vector<std::byte>> document_seeds() {
    const std::vector<std::vector<unsigned>> raw = {
        {0xA3, 0x61, 'a', 0x01, 0x61, 'b', 0x82, 0xFB, 0x3F, 0xF8, 0,   0,
         0,    0,    0,   0,    0x61, 'x', 0x61, 'c',  0xA1, 0x61, 'd', 0xF5},
        {0xA2, 0x64, 'u',  'n',  'i',  't',  0x62, 'm', 'm', 0x65, 'r', 'a', 'n', 'g',
         'e',  0x82, 0xF9, 0x3C, 0x00, 0x3B, 0,    0,   0,   0,    0,   0,   0,   0x09},
        {0xA1, 0x61, 'n', 0x81, 0x81, 0x81, 0x81, 0x81, 0x00},
    };
    std::vector<std::vector<std::byte>> seeds;
    for (const auto &doc : raw) {
        std::vector<std::byte> bytes;
        for (const unsigned b : doc)
            bytes.push_back(static_cast<std::byte>(b));
        seeds.push_back(std::move(bytes));
    }
    return seeds;
}

} // namespace

TEST_CASE("description tables with random damage are parsed or refused") {
    const std::vector<table_seed> seeds = table_seeds();
    for (std::size_t i = 0; i < seeds.size(); ++i) {
        INFO("seed " << i);
        type_table table;
        CHECK(table.parse(seeds[i].table.span(), {&seeds[i].entry, 1}, seeds[i].max_arrays) == status::ok);
    }
    std::mt19937_64 rng(seed);
    std::vector<std::byte> line(64);
    int parsed = 0;
    for (int round = 0; round < rounds; ++round) {
        const table_seed &s = seeds[rng() % seeds.size()];
        std::vector<std::byte> bytes = s.table.bytes;
        mutate(bytes, rng, true);
        std::uint32_t entry = s.entry;
        if (rng() % 8 == 0)
            entry ^= std::uint32_t{1} << (rng() % 32);
        type_table table;
        if (table.parse(bytes, {&entry, 1}, s.max_arrays) != status::ok)
            continue;
        ++parsed;
        if (table.field(0).kind == kind_max_array) {
            for (std::byte &b : line)
                b = random_byte(rng);
            static_cast<void>(detail::max_array_bytes(table, table.field(0), line.data()));
        }
    }
    // Some damaged tables still parse, so the parse is walked to its end and its users run.
    CHECK(parsed > 0);
}

TEST_CASE("metadata documents with random damage are checked without reading past them") {
    const std::vector<std::vector<std::byte>> seeds = document_seeds();
    for (const auto &doc : seeds)
        REQUIRE(metadata_ok(doc));
    std::mt19937_64 rng(seed);
    int accepted = 0;
    for (int round = 0; round < rounds; ++round) {
        // A copy of exactly the document's size, so the sanitizer sees a read past its end.
        std::vector<std::byte> bytes = seeds[rng() % seeds.size()];
        mutate(bytes, rng, false);
        const std::unique_ptr<std::byte[]> exact(new std::byte[bytes.size()]);
        std::memcpy(exact.get(), bytes.data(), bytes.size());
        accepted += metadata_ok({exact.get(), bytes.size()}) ? 1 : 0;
    }
    CHECK(accepted > 0);
}

TEST_CASE("a box segment with random damage is opened and read or refused") {
    const table_builder types = int_list();
    const field_spec fields[2] = {{0, 8, kind_int}, {8, 0, kind_list}};
    const std::vector<std::byte> document = document_seeds()[0];
    const metadata_spec metadata{document, 4096, 4};
    const std::string name = unique("mutations");
    auto owner = handle::create(name, fields, 40, 1, 4, {}, types.span(), metadata);
    REQUIRE(owner.has_value());
    auto *base = static_cast<std::byte *>(owner->base());
    const std::uint64_t size = reinterpret_cast<const header *>(base)->common.size;
    const std::uint64_t record = reinterpret_cast<const header *>(base)->record;
    const std::vector<std::byte> original(base, base + size);
    std::vector<std::byte> buf(size);
    std::mt19937_64 rng(seed);
    int opened_count = 0;
    // Each open maps the segment again, so fewer rounds than the in-memory parsers.
    for (int round = 0; round < rounds / 10; ++round) {
        std::memcpy(base, original.data(), size);
        const int changes = 1 + static_cast<int>(rng() % 8);
        for (int i = 0; i < changes; ++i) {
            // Past the magic, which an open waits for while it is 0, and half the time before the record, in
            // the header, the field table, the waiter slots and the description table.
            const std::size_t end = rng() % 2 == 0 ? record : size;
            const std::size_t at = 8 + rng() % (end - 8);
            base[at] = rng() % 2 == 0 ? random_byte(rng) : base[at] ^ static_cast<std::byte>(1u << (rng() % 8));
        }
        // An odd seq reads as a writer holding the lock, which every read would wait out.
        auto &seq = reinterpret_cast<header *>(base)->seq;
        seq &= ~std::uint64_t{1};
        auto opened = handle::open(name, seconds(0.01));
        if (!opened)
            continue;
        ++opened_count;
        static_cast<void>(opened->read_record_used(buf));
        static_cast<void>(opened->read_used(1, buf));
        const std::uint64_t head = opened->metadata_head();
        if (head != 0)
            static_cast<void>(opened->read_metadata(head, buf));
    }
    // Damage reaches both the refusals of the open and the reads of a segment it accepts.
    CHECK(opened_count > 0);
    CHECK(opened_count < rounds / 10);
    std::memcpy(base, original.data(), size);
    static_cast<void>(unlink(name));
}
