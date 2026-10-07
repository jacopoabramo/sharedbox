// Writing an array field in place: begin_write takes the lock and hands out the field's bytes, end_write
// counts the write, releases the lock and wakes waiters.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "table_builder.hpp"
#include "unique.hpp"

#include <array>
#include <cstring>
#include <string>
#include <thread>

using namespace sharedbox;

namespace {

// An array of 4 float32 values: its description alone, at offset 0.
table_builder four_floats() {
    table_builder t;
    t.head(kind_array, 0, 16);
    t.put(dl_dtype{2, 32, 1});
    t.put(std::uint8_t{1});
    t.put(std::uint8_t{0});
    t.put(std::uint16_t{0});
    t.put(std::uint64_t{4});
    t.pad();
    return t;
}

std::uint64_t seq_of(const handle &h) { return static_cast<const header *>(h.base())->seq; }

} // namespace

TEST_CASE("begin_write hands out an array field, and end_write counts it and wakes a waiter") {
    const table_builder t = four_floats();
    const std::string name = unique("write-in-place");
    const field_spec fields[1] = {{0, 0, kind_array}};
    auto h = handle::create(name, fields, 16, 1, 4, {}, t.span());
    REQUIRE(h.has_value());
    const auto slot = h->register_waiter();
    REQUIRE(slot.has_value());
    const std::uint64_t generation = h->generation();
    result<wake> woken = sharedbox::unexpected(status::ok);
    std::thread waiter([&] { woken = h->wait(*slot, generation, seconds(5.0)); });
    const auto w = h->begin_write(0, seconds(1.0));
    REQUIRE(w.has_value());
    CHECK(w->bytes.size() == 16);
    CHECK((seq_of(*h) & 1u) == 1u);
    const float values[4] = {1.0f, 2.0f, 3.0f, 4.0f};
    std::memcpy(w->bytes.data(), values, sizeof values);
    h->end_write(*w);
    waiter.join();
    CHECK((woken.has_value() && *woken == wake::changed));
    CHECK((seq_of(*h) & 1u) == 0u);
    CHECK((h->version(0) == 1 && h->generation() == generation + 1));
    std::array<std::byte, 16> buf{};
    const auto read = h->read(0, buf);
    CHECK((read && read->len == 16 && read->version == 1 && std::memcmp(buf.data(), values, 16) == 0));
    h->release_waiter(*slot);
    static_cast<void>(unlink(name));
}

TEST_CASE("begin_write refuses a field that is not an array and times out on a held lock") {
    const table_builder t = four_floats();
    const std::string name = unique("write-in-place-refused");
    const field_spec fields[2] = {{0, 0, kind_array}, {16, 8, kind_int}};
    auto h = handle::create(name, fields, 24, 1, 4, {}, t.span());
    REQUIRE(h.has_value());
    CHECK(h->begin_write(1, seconds(1.0)).error() == status::range);
    CHECK(h->begin_write(2, seconds(1.0)).error() == status::range);
    auto other = handle::open(name, seconds(1.0));
    REQUIRE(other.has_value());
    const auto held = h->begin_write(0, seconds(1.0));
    REQUIRE(held.has_value());
    CHECK(other->begin_write(0, seconds(0.05)).error() == status::lock_timeout);
    h->end_write(*held);
    const auto next = other->begin_write(0, seconds(1.0));
    REQUIRE(next.has_value());
    other->end_write(*next);
    CHECK(h->version(0) == 2);
    static_cast<void>(unlink(name));
}
