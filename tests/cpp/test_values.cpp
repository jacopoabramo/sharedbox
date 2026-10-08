// The C++ form of the new kinds: encode and decode with their checks, list writes and reads of the used
// slots, and the copies that run the wait hooks around themselves.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "table_builder.hpp"
#include "unique.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstring>
#include <memory>
#include <string>
#include <thread>
#include <vector>

using namespace sharedbox;
using namespace std::chrono;

namespace {

std::array<std::byte, 16> datetime_bytes(std::int64_t micros, std::int16_t offset, std::uint8_t flags) {
    std::array<std::byte, 16> out{};
    std::memcpy(out.data(), &micros, 8);
    std::memcpy(out.data() + 8, &offset, 2);
    out[10] = static_cast<std::byte>(flags);
    return out;
}

// A list of `capacity` ints; the table is the list's description alone.
table_builder int_list(std::uint32_t capacity) {
    table_builder t;
    t.head(kind_list, 1, 8 + 8 * capacity);
    t.put(capacity);
    t.entry(8, kind_int, 8);
    t.pad();
    return t;
}

int hooks_before = 0;
int hooks_after = 0;

} // namespace

TEST_CASE("datetime values round trip and bad stored values are refused") {
    const datetime_value aware{1700000000000000, 60, false, false};
    std::array<std::byte, 16> out{};
    REQUIRE(encode_datetime(aware, out) == status::ok);
    const auto back = decode_datetime(out);
    REQUIRE(back.has_value());
    CHECK((back->micros == aware.micros && back->offset == 60 && !back->naive));
    CHECK(back->local().time_since_epoch() - back->utc().time_since_epoch() == minutes(60));
    CHECK(decode_datetime(datetime_bytes(0, 0, 4)).error().code == status::corrupt);
    CHECK(decode_datetime(datetime_bytes(0, 1440, 0)).error().code == status::corrupt);
    CHECK(decode_datetime(datetime_bytes(0, 30, 1)).error().code == status::corrupt);
    CHECK(decode_datetime(datetime_bytes(253402300800000000, 0, 1)).error().code == status::corrupt);
    CHECK(encode_datetime({253402300800000000, 0, true, false}, out) == status::range);
    CHECK(decode_datetime(std::span(out).first(12)).error().code == status::range);
}

TEST_CASE("dates, times and timedeltas keep their ranges") {
    std::array<std::byte, 4> date{};
    REQUIRE(encode_date(sys_days(year{1} / January / 1), date) == status::ok);
    std::int32_t ordinal = 0;
    std::memcpy(&ordinal, date.data(), 4);
    CHECK(ordinal == 1);
    CHECK(encode_date(sys_days(year{10000} / January / 1), date) == status::range);
    ordinal = 0;
    std::memcpy(date.data(), &ordinal, 4);
    CHECK(decode_date(date).error().code == status::corrupt);

    std::array<std::byte, 16> time{};
    CHECK(encode_time({microseconds(micros_per_day), 0, true, false}, time) == status::range);
    REQUIRE(encode_time({hours(23), -90, false, true}, time) == status::ok);
    const auto t = decode_time(time);
    REQUIRE(t.has_value());
    CHECK((t->of_day == hours(23) && t->offset == -90 && t->fold));

    std::array<std::byte, 12> delta{};
    CHECK(encode_timedelta({0, 86400, 0}, delta) == status::range);
    REQUIRE(encode_timedelta({-999999999, 0, 0}, delta) == status::ok);
    CHECK(decode_timedelta(delta)->days == -999999999);
}

TEST_CASE("positions, tags, presence and lengths stay below their counts") {
    // An enum of 2 names, a union of int and float, and a list of 3 ints, one field each.
    table_builder t;
    t.head(kind_enum, 2, 2);
    t.name("A");
    t.name("B");
    t.pad();
    const std::uint32_t union_at = t.head(kind_union, 2, 16);
    t.entry(8, kind_int, 8);
    t.entry(8, kind_float, 8);
    const std::uint32_t list_at = t.head(kind_list, 1, 32);
    t.put(std::uint32_t{3});
    t.entry(8, kind_int, 8);
    t.pad();
    const std::string name = unique("values-enum");
    const field_spec fields[3] = {{48, 0, kind_enum}, {0, union_at, kind_union}, {16, list_at, kind_list}};
    auto box = handle::create(name, fields, 56, 1, 4, {}, t.span());
    REQUIRE(box.has_value());
    const type_view view = box->field_type(0);
    std::array<std::byte, 2> pos{};
    CHECK(encode_position(view, 2, pos) == status::range);
    REQUIRE(encode_position(view, 1, pos) == status::ok);
    CHECK(*decode_position(view, pos) == 1);
    pos[0] = std::byte{2};
    const auto position = decode_position(view, pos);
    REQUIRE_FALSE(position);
    CHECK(position.error().code == status::corrupt);
    const std::array<std::byte, 1> two{std::byte{2}};
    const auto present = decode_present(two);
    REQUIRE_FALSE(present);
    CHECK(present.error().code == status::corrupt);
    const auto flag = decode_bool(two);
    REQUIRE_FALSE(flag);
    CHECK(flag.error().code == status::corrupt);

    const type_view either = box->field_type(1);
    std::array<std::byte, 16> tagged{};
    tagged[0] = std::byte{1};
    CHECK(*decode_tag(either, tagged) == 1);
    tagged[0] = std::byte{2};
    const auto tag = decode_tag(either, tagged);
    REQUIRE_FALSE(tag);
    CHECK(tag.error().code == status::corrupt);

    const type_view list = box->field_type(2);
    std::array<std::byte, 32> items{};
    const std::uint32_t three = 3;
    std::memcpy(items.data(), &three, 4);
    CHECK(*decode_length(list, items) == 3);
    // Three elements need 32 bytes; 24 cannot hold them.
    const auto short_bytes = decode_length(list, std::span<const std::byte>(items.data(), 24));
    REQUIRE_FALSE(short_bytes);
    CHECK(short_bytes.error().code == status::corrupt);
    const std::uint32_t four = 4;
    std::memcpy(items.data(), &four, 4);
    const auto too_long = decode_length(list, items);
    REQUIRE_FALSE(too_long);
    CHECK(too_long.error().code == status::corrupt);
    static_cast<void>(unlink(name));
}

TEST_CASE("a list field takes its length and used slots, and read_used copies only those") {
    const table_builder t = int_list(3);
    const std::string name = unique("values-list");
    const field_spec fields[1] = {{0, 0, kind_list}};
    auto box = handle::create(name, fields, 32, 1, 4, {}, t.span());
    REQUIRE(box.has_value());
    std::byte bytes[16]{};
    const std::uint32_t one = 1;
    const std::int64_t seven = 7;
    std::memcpy(bytes, &one, 4);
    std::memcpy(bytes + 8, &seven, 8);
    const value v{0, bytes};
    REQUIRE(box->write({&v, 1}, sharedbox::seconds(1.0)).has_value());
    std::byte got[32]{};
    const auto read = box->read_used(0, got);
    REQUIRE(read.has_value());
    CHECK(read->len == 16);
    CHECK(std::memcmp(got, bytes, 16) == 0);
    CHECK(*decode_length(box->field_type(0), std::span<const std::byte>(got, 16)) == 1);
    // A length past the capacity, and bytes that do not match the length, are refused.
    const std::uint32_t four = 4;
    std::memcpy(bytes, &four, 4);
    CHECK(box->write({&v, 1}, sharedbox::seconds(1.0)).error().code == status::range);
    const std::uint32_t two = 2;
    std::memcpy(bytes, &two, 4);
    CHECK(box->write({&v, 1}, sharedbox::seconds(1.0)).error().code == status::range);
    CHECK(box->read_used(1, got).error().code == status::range);
    static_cast<void>(unlink(name));
}

TEST_CASE("large copies run the wait hooks once around themselves") {
    // 1 Mi ints: 8 MiB and 8 bytes, three copy chunks.
    const std::uint32_t capacity = max_capacity;
    const table_builder t = int_list(capacity);
    const std::string name = unique("values-large");
    const field_spec fields[1] = {{0, 0, kind_list}};
    const std::uint32_t size = 8 + 8 * capacity;
    auto box = handle::create(name, fields, size, 1, 4, {}, t.span());
    REQUIRE(box.has_value());
    auto other = handle::open(name, sharedbox::seconds(1.0));
    REQUIRE(other.has_value());
    box->set_wait_hooks(
        []() -> void * {
            ++hooks_before;
            return nullptr;
        },
        [](void *) { ++hooks_after; });
    std::vector<std::byte> bytes(size);
    std::memcpy(bytes.data(), &capacity, 4);
    for (std::uint32_t i = 0; i < capacity; ++i) {
        const std::int64_t n = i;
        std::memcpy(bytes.data() + 8 + std::size_t{i} * 8, &n, 8);
    }
    const value v{0, bytes};

    // Another handle holds the write lock for a while, so each call has to wait.
    const auto contended = [&](auto &&call) {
        hooks_before = hooks_after = 0;
        const auto locked = other->lock(sharedbox::seconds(1.0));
        REQUIRE(locked.has_value());
        std::thread releaser([&] {
            std::this_thread::sleep_for(milliseconds(50));
            other->unlock(*locked);
        });
        call();
        releaser.join();
        CHECK((hooks_before == 1 && hooks_after == 1));
    };

    contended([&] { REQUIRE(box->write_large({&v, 1}, sharedbox::seconds(5.0)).has_value()); });
    std::vector<std::byte> got(size);
    contended([&] {
        const auto read = box->read_large(0, got);
        REQUIRE(read.has_value());
        CHECK((read->len == size && read->version == 1));
    });
    CHECK(got == bytes);
    std::fill(got.begin(), got.end(), std::byte{0});
    contended([&] {
        const auto read = box->read_used(0, got);
        REQUIRE(read.has_value());
        CHECK(read->len == size);
    });
    CHECK(got == bytes);
    std::vector<std::byte> record(box->record_size());
    hooks_before = hooks_after = 0;
    REQUIRE(box->read_record_large(record).has_value());
    CHECK(std::memcmp(record.data(), bytes.data(), size) == 0);
    CHECK((hooks_before == 1 && hooks_after == 1));
    static_cast<void>(unlink(name));
}
