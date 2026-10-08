// The description table: valid tables parse into the types type_view shows, forged ones are refused.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "table_builder.hpp"
#include "unique.hpp"

#include <string>

using sharedbox::handle;
using sharedbox::seconds;
using sharedbox::status;
using sharedbox::detail::type_table;
using namespace sharedbox;

namespace {

// enum Colour { RED, GREEN }: one description at offset 0.
table_builder colour() {
    table_builder t;
    t.head(kind_enum, 2, 2);
    t.name("RED");
    t.name("GREEN");
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

status parse(const table_builder &t, std::uint32_t entry) {
    type_table table;
    return table.parse(t.span(), {&entry, 1}, false);
}

// A chain of n optionals around an int, each description after the one that refers to it. Level k from the
// inside takes 8 + 8k bytes: a presence byte padded to 8, then the level below.
table_builder optionals(int n) {
    table_builder t;
    std::uint32_t size = 8 + 8 * static_cast<std::uint32_t>(n);
    for (int i = 0; i < n; ++i) {
        const std::uint32_t at = t.head(kind_optional, 1, size);
        const bool last = i == n - 1;
        t.entry(8, last ? kind_int : kind_optional, last ? 8 : at + 16);
        size -= 8;
    }
    return t;
}

} // namespace

TEST_CASE("an enum field opens with its names") {
    const table_builder t = colour();
    const std::string name = unique("types-enum");
    const field_spec fields[1] = {{0, 0, kind_enum}};
    auto owner = handle::create(name, fields, 8, 1, 4, {}, t.span());
    REQUIRE(owner.has_value());
    auto other = handle::open(name, seconds(1.0));
    REQUIRE(other.has_value());
    CHECK(other->field(0).capacity == 2);
    const type_view view = other->field_type(0);
    CHECK((view.kind() == kind_enum && view.count() == 2 && view.size() == 2 && view.alignment() == 2));
    CHECK(view.name(1) == "GREEN");
    CHECK(other->types_table().size() == t.bytes.size());
    auto copy = other->duplicate();
    REQUIRE(copy.has_value());
    CHECK(copy->field_type(0).name(0) == "RED");
    static_cast<void>(unlink(name));
}

TEST_CASE("a record holding a list shows its members") {
    type_table table;
    const table_builder t = record_with_list();
    const std::uint32_t entry = entry_of(kind_record, 0);
    REQUIRE(table.parse(t.span(), {&entry, 1}, false) == status::ok);
    const detail::type_ref &record = table.field(0);
    CHECK((record.kind == kind_record && record.size == 40 && record.alignment == 8));
    const detail::type_node &node = table.node(record.node);
    CHECK(node.count == 2);
    const detail::type_member &list = table.member(node.members + 1);
    CHECK((list.offset == 8 && list.type.kind == kind_list && list.type.size == 32));
    const detail::type_node &list_node = table.node(list.type.node);
    CHECK((list_node.capacity == 3 && list_node.slots == 8 && list_node.stride == 8));
    CHECK(table.name(node.names + 1) == "b");
}

TEST_CASE("type_view gives empty values for another kind or an index past count") {
    const table_builder t = record_with_list();
    const std::string name = unique("types-view");
    const field_spec fields[1] = {{0, 0, kind_record}};
    auto box = handle::create(name, fields, 40, 1, 4, {}, t.span());
    REQUIRE(box.has_value());
    const type_view record = box->field_type(0);
    const type_view list = record.member(1);
    CHECK((list.kind() == kind_list && record.member_offset(1) == 8 && record.name(1) == "b"));
    CHECK((!record.member(2).described() && record.member(2).size() == 0));
    CHECK(record.member_offset(2) == 0);
    CHECK(record.name(2).empty());
    CHECK(record.flag_bits(0) == 0);
    CHECK((record.literal(0).tag == 0 && record.literal(0).text.empty()));
    CHECK(list.member(0).kind() == kind_int);
    CHECK(list.member(1).size() == 0);
    CHECK(list.name(0).empty());
    CHECK(record.member(0).member(0).size() == 0);
    static_cast<void>(unlink(name));
}

TEST_CASE("forged tables are refused") {
    SUBCASE("a child that points back at its parent") {
        table_builder t;
        t.head(kind_optional, 1, 16);
        t.entry(8, kind_optional, 0);
        CHECK(parse(t, entry_of(kind_optional, 0)) == status::corrupt);
    }
    SUBCASE("two fields sharing one description") {
        const table_builder t = colour();
        const std::uint32_t entries[2] = {entry_of(kind_enum, 0), entry_of(kind_enum, 0)};
        type_table table;
        CHECK(table.parse(t.span(), entries, false) == status::corrupt);
    }
    SUBCASE("nesting deeper than 16") {
        CHECK(parse(optionals(16), entry_of(kind_optional, 0)) == status::ok);
        CHECK(parse(optionals(17), entry_of(kind_optional, 0)) == status::corrupt);
    }
    SUBCASE("a union of 256 members") {
        table_builder t;
        t.head(kind_union, 256, 16);
        for (int i = 0; i < 256; ++i)
            t.entry(8, kind_int, 8);
        CHECK(parse(t, entry_of(kind_union, 0)) == status::corrupt);
    }
    SUBCASE("array dimensions whose product overflows") {
        table_builder t;
        t.head(kind_array, 0, 64);
        t.put(dl_dtype{2, 32, 1});
        t.put(std::uint8_t{2});
        t.put(std::uint8_t{0});
        t.put(std::uint16_t{0});
        t.put(std::uint64_t{1} << 20);
        t.put(std::uint64_t{1} << 20);
        CHECK(parse(t, entry_of(kind_array, 0)) == status::corrupt);
    }
    SUBCASE("a list whose size does not match its capacity") {
        table_builder t;
        t.head(kind_list, 1, 40);
        t.put(std::uint32_t{3});
        t.entry(8, kind_int, 8);
        t.pad();
        CHECK(parse(t, entry_of(kind_list, 0)) == status::corrupt);
    }
    SUBCASE("flags that are not zero") {
        table_builder t = colour();
        t.bytes[1] = std::byte{1};
        CHECK(parse(t, entry_of(kind_enum, 0)) == status::corrupt);
    }
    SUBCASE("a reference inside a record") {
        table_builder t;
        t.head(kind_record, 1, 144);
        t.entry(0, kind_ref, 144);
        t.name("r");
        t.pad();
        CHECK(parse(t, entry_of(kind_record, 0)) == status::corrupt);
    }
    SUBCASE("an array as a list element") {
        table_builder t;
        t.head(kind_list, 1, 64 + 64);
        t.put(std::uint32_t{1});
        t.entry(64, kind_array, 24);
        t.pad();
        t.head(kind_array, 0, 64);
        t.put(dl_dtype{2, 32, 1});
        t.put(std::uint8_t{1});
        t.put(std::uint8_t{0});
        t.put(std::uint16_t{0});
        t.put(std::uint64_t{16});
        CHECK(parse(t, entry_of(kind_list, 0)) == status::corrupt);
    }
    SUBCASE("record members that overlap") {
        table_builder t;
        t.head(kind_record, 2, 16);
        t.entry(0, kind_int, 8);
        t.entry(4, kind_date, 4);
        t.name("a");
        t.name("b");
        t.pad();
        CHECK(parse(t, entry_of(kind_record, 0)) == status::corrupt);
    }
    SUBCASE("a list stride that wraps past 32 bits") {
        table_builder t;
        t.head(kind_list, 1, 4);
        const std::size_t outer = t.bytes.size();
        t.put(std::uint32_t{1});
        t.entry(4, kind_list, 0);
        t.pad();
        const std::uint32_t inner = t.head(kind_list, 1, 0xFFFFFFFEu);
        t.put(std::uint32_t{10});
        t.entry(4, kind_record, 0);
        t.pad();
        const std::uint32_t rec = t.head(kind_record, 1, 429496729);
        t.entry(0, kind_bool, 1);
        t.name("x");
        t.pad();
        t.patch(outer + 8, entry_of(kind_list, inner));
        t.patch(inner + 8 + 4 + 4, entry_of(kind_record, rec));
        CHECK(parse(t, entry_of(kind_list, 0)) == status::corrupt);
    }
    SUBCASE("an enum name that is not UTF-8") {
        table_builder t;
        t.head(kind_enum, 1, 2);
        t.put(std::uint16_t{2});
        t.put(std::uint8_t{0xC0});
        t.put(std::uint8_t{0x80});
        t.pad();
        CHECK(parse(t, entry_of(kind_enum, 0)) == status::corrupt);
    }
    SUBCASE("a description that is not 8-aligned") {
        const table_builder t = colour();
        CHECK(parse(t, entry_of(kind_enum, 4)) == status::corrupt);
    }
    SUBCASE("a table whose size is not a multiple of 8") {
        table_builder t = colour();
        t.bytes.push_back(std::byte{0});
        CHECK(parse(t, entry_of(kind_enum, 0)) == status::corrupt);
    }
}

TEST_CASE("a described kind this header does not know opens as opaque bytes") {
    const table_builder t = colour();
    const std::string name = unique("types-opaque");
    const field_spec fields[1] = {{0, 0, kind_enum}};
    auto owner = handle::create(name, fields, 8, 1, 4, {}, t.span());
    REQUIRE(owner.has_value());
    auto *bytes = static_cast<std::byte *>(owner->base());
    stored_field stored;
    std::memcpy(&stored, bytes + header_size, sizeof stored);
    stored.capacity_and_kind = entry_of(80, 0);
    std::memcpy(bytes + header_size, &stored, sizeof stored);
    const std::uint64_t table = detail::tail_end(1, 4);
    bytes[table] = std::byte{80};
    auto other = handle::open(name, seconds(1.0));
    REQUIRE(other.has_value());
    CHECK((other->field(0).kind == 80 && other->field(0).capacity == 2));
    CHECK_FALSE(other->field_type(0).described());
    static_cast<void>(unlink(name));
}

TEST_CASE("create refuses a table that does not check") {
    table_builder t = colour();
    t.bytes[1] = std::byte{1};
    const field_spec fields[1] = {{0, 0, kind_enum}};
    const auto created = handle::create(unique("types-bad"), fields, 8, 1, 4, {}, t.span());
    REQUIRE_FALSE(created);
    CHECK(created.error().code == status::range);
}

TEST_CASE("layout 1.0 refuses a described kind") {
    const field_spec fields[1] = {{0, 8, kind_int}};
    const std::string name = unique("types-v1");
    SUBCASE("on open") {
        auto owner = detail::create_impl(name, fields, 8, 1, 4, {}, {}, 1, true);
        REQUIRE(owner.has_value());
        auto *bytes = static_cast<std::byte *>(owner->base());
        stored_field stored;
        std::memcpy(&stored, bytes + header_size, sizeof stored);
        stored.capacity_and_kind = entry_of(kind_enum, 8);
        std::memcpy(bytes + header_size, &stored, sizeof stored);
        const auto opened = handle::open(name, seconds(1.0));
        REQUIRE_FALSE(opened);
        CHECK(opened.error().code == status::corrupt);
        static_cast<void>(unlink(name));
    }
    SUBCASE("on create") {
        const field_spec bad[1] = {{0, 8, kind_enum}};
        const auto created = detail::create_impl(name, bad, 8, 1, 4, {}, {}, 1, true);
        REQUIRE_FALSE(created);
        CHECK(created.error().code == status::range);
    }
}
