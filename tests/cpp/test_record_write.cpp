// Writing several fields under one hold of the lock: begin_record_write, write_held, then
// end_record_write counting the changed fields or abort_record_write counting none.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "unique.hpp"

#include <array>
#include <cstring>
#include <string>

using namespace sharedbox;

namespace {

constexpr field_spec fields[2] = {{0, 8, kind_int}, {8, 8, kind_int}};

std::uint64_t seq_of(const handle &h) { return static_cast<const header *>(h.base())->seq; }

std::int64_t int_of(const handle &h, std::uint16_t field) {
    std::array<std::byte, 8> buf{};
    REQUIRE(h.read(field, buf).has_value());
    std::int64_t number = 0;
    std::memcpy(&number, buf.data(), sizeof number);
    return number;
}

} // namespace

TEST_CASE("end_record_write counts each changed field once") {
    const std::string name = unique("record-write");
    auto h = handle::create(name, fields, 16, 1, 1, {});
    REQUIRE(h.has_value());
    const std::uint64_t generation = h->generation();
    const auto w = h->begin_record_write(seconds(1.0));
    REQUIRE(w.has_value());
    std::int64_t first = 1;
    std::int64_t second = 2;
    std::int64_t third = 3;
    const value a[1] = {{0, std::as_bytes(std::span(&first, 1))}};
    const value b[2] = {{0, std::as_bytes(std::span(&second, 1))}, {1, std::as_bytes(std::span(&third, 1))}};
    CHECK(h->write_held(*w, a).has_value());
    CHECK(h->write_held(*w, b).has_value());
    CHECK((h->version(0) == 0 && h->version(1) == 0));
    const std::uint16_t changed[2] = {0, 1};
    h->end_record_write(*w, changed);
    CHECK((h->version(0) == 1 && h->version(1) == 1 && h->generation() == generation + 1));
    CHECK((int_of(*h, 0) == 2 && int_of(*h, 1) == 3));
    static_cast<void>(unlink(name));
}

TEST_CASE("abort_record_write leaves counts and moves the generation by one") {
    const std::string name = unique("record-write-abort");
    auto h = handle::create(name, fields, 16, 1, 1, {});
    REQUIRE(h.has_value());
    const std::int64_t old_number = 7;
    const value old_value[1] = {{0, std::as_bytes(std::span(&old_number, 1))}};
    REQUIRE(h->write(old_value, seconds(1.0)).has_value());
    const std::uint64_t seq = seq_of(*h);
    const std::uint64_t generation = h->generation();
    const auto w = h->begin_record_write(seconds(1.0));
    REQUIRE(w.has_value());
    const std::span<std::byte> slot = h->held_bytes(*w, 0);
    REQUIRE(slot.size() == 8);
    std::array<std::byte, 8> saved{};
    std::memcpy(saved.data(), slot.data(), saved.size());
    const std::int64_t number = 99;
    const value changed[1] = {{0, std::as_bytes(std::span(&number, 1))}};
    REQUIRE(h->write_held(*w, changed).has_value());
    std::memcpy(slot.data(), saved.data(), saved.size());
    h->abort_record_write(*w);
    CHECK((h->version(0) == 1 && h->version(1) == 0));
    CHECK((h->generation() == generation + 1 && seq_of(*h) == seq + 2));
    CHECK(int_of(*h, 0) == old_number);
    static_cast<void>(unlink(name));
}

TEST_CASE("held_bytes spans a prefixed field with its length prefix") {
    constexpr field_spec mixed[2] = {{0, 8, kind_int}, {8, 16, kind_bytes}};
    const std::string name = unique("record-write-span");
    auto h = handle::create(name, mixed, 32, 1, 1, {});
    REQUIRE(h.has_value());
    const auto w = h->begin_record_write(seconds(1.0));
    REQUIRE(w.has_value());
    CHECK(h->held_bytes(*w, 0).size() == 8);
    CHECK(h->held_bytes(*w, 1).size() == 20);
    h->abort_record_write(*w);
    static_cast<void>(unlink(name));
}

TEST_CASE("write_held refuses a field past field_count") {
    const std::string name = unique("record-write-range");
    auto h = handle::create(name, fields, 16, 1, 1, {});
    REQUIRE(h.has_value());
    const auto w = h->begin_record_write(seconds(1.0));
    REQUIRE(w.has_value());
    CHECK(h->held_bytes(*w, 2).empty());
    const value missing[1] = {{2, {}}};
    CHECK(h->write_held(*w, missing).error().code == status::range);
    h->abort_record_write(*w);
    static_cast<void>(unlink(name));
}

TEST_CASE("begin_record_write times out while another writer holds the lock") {
    const std::string name = unique("record-write-timeout");
    auto h = handle::create(name, fields, 16, 1, 1, {});
    REQUIRE(h.has_value());
    auto other = h->duplicate();
    REQUIRE(other.has_value());
    const auto held = other->begin_record_write(seconds(1.0));
    REQUIRE(held.has_value());
    CHECK(h->begin_record_write(seconds(0.01)).error().code == status::lock_timeout);
    other->abort_record_write(*held);
    const auto next = h->begin_record_write(seconds(1.0));
    CHECK(next.has_value());
    h->abort_record_write(*next);
    static_cast<void>(unlink(name));
}
