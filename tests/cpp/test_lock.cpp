// The sequence lock: reads and writes, and the unlock of a writer that force_unlock released.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "unique.hpp"

#include <array>
#include <cstring>

using sharedbox::handle;
using sharedbox::seconds;
using sharedbox::status;

namespace {

constexpr sharedbox::field_spec fields[2] = {{0, 8, sharedbox::kind_int}, {8, 16, sharedbox::kind_bytes}};

std::uint64_t seq_of(const handle &h) { return static_cast<const sharedbox::header *>(h.base())->seq; }

// A writer that was force-unlocked but is still running must not release the next writer's lock, and
// the generation never goes backwards on the way.
TEST_CASE("a late unlock leaves the next writer locked") {
    const std::string name = unique("lock");
    auto a = handle::create(name, fields, 32, 1, 1, {});
    auto b = handle::open(name, seconds(1.0));
    REQUIRE((a && b));
    const auto locked_a = a->lock(seconds(1.0));
    CHECK((locked_a && b->writer_pid() == sharedbox::current_pid()));
    const std::uint64_t g0 = b->generation();
    CHECK(b->force_unlock().has_value());
    CHECK(b->writer_pid() == sharedbox::current_pid());
    const std::uint64_t g1 = b->generation();
    const auto locked_b = b->lock(seconds(1.0));
    CHECK(locked_b.has_value());
    const std::uint64_t before = seq_of(*b);
    a->unlock(*locked_a);
    CHECK((seq_of(*b) == before && (seq_of(*b) & 1u) == 1));
    CHECK(b->writer_pid() == sharedbox::current_pid());
    const std::uint64_t g2 = b->generation();
    b->unlock(*locked_b);
    CHECK((seq_of(*b) == before + 1 && b->writer_pid() == 0));
    const std::uint64_t g3 = b->generation();
    CHECK((g0 <= g1 && g1 <= g2 && g2 < g3));
    CHECK(g3 == (*locked_b + 2) >> 1);
    static_cast<void>(sharedbox::unlink(name));
}

TEST_CASE("write then read") {
    const std::string name = unique("readwrite");
    auto h = handle::create(name, fields, 32, 1, 1, {});
    REQUIRE(h.has_value());
    const std::int64_t number = -5;
    const char text[] = "twelve bytes";
    const sharedbox::value values[2] = {{0, std::as_bytes(std::span(&number, 1))},
                                        {1, std::as_bytes(std::span(text, 12))}};
    CHECK(h->write(values, seconds(1.0)).has_value());
    CHECK((h->generation() == 1 && h->version(0) == 1 && h->version(1) == 1));
    std::array<std::byte, 16> buf{};
    const auto read = h->read(1, buf);
    CHECK((read && read->len == 12 && read->version == 1 && std::memcmp(buf.data(), text, 12) == 0));
    // Too small a buffer: nothing is copied and len says what is needed.
    std::array<std::byte, 4> four{};
    const auto partial = h->read(1, four);
    CHECK((partial && partial->len == 12 && four[0] == std::byte{0}));
    CHECK(h->read(2, buf).error() == status::range);
    std::array<std::byte, 32> record{};
    const auto generation = h->read_record(record);
    CHECK((generation && *generation == 1));
    const auto payload = h->payload(1, record);
    CHECK((payload.size() == 12 && std::memcmp(payload.data(), text, 12) == 0));
    CHECK(h->read_record({record.data(), 31}).error() == status::range);
    // A value of the wrong size or for a missing field changes nothing.
    const sharedbox::value wrong[1] = {{0, std::as_bytes(std::span(text, 4))}};
    CHECK(h->write(wrong, seconds(1.0)).error() == status::range);
    const sharedbox::value missing[1] = {{2, {}}};
    CHECK(h->write(missing, seconds(1.0)).error() == status::range);
    CHECK(h->write(values, seconds(0)).error() == status::range);
    CHECK(h->generation() == 1);
    static_cast<void>(sharedbox::unlink(name));
}

TEST_CASE("a held lock times out") {
    const std::string name = unique("timeout");
    auto a = handle::create(name, fields, 32, 1, 1, {});
    REQUIRE(a.has_value());
    const auto locked = a->lock(seconds(1.0));
    CHECK(locked.has_value());
    const std::int64_t number = 1;
    const sharedbox::value value{0, std::as_bytes(std::span(&number, 1))};
    CHECK(a->write({&value, 1}, seconds(0.05)).error() == status::lock_timeout);
    std::array<std::byte, 8> buf{};
    CHECK(a->set_lock_timeout(seconds(0.05)).has_value());
    CHECK(a->read(0, buf).error() == status::lock_timeout);
    a->unlock(*locked);
    CHECK(a->read(0, buf).has_value());
    static_cast<void>(sharedbox::unlink(name));
}

} // namespace
