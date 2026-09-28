// Creating, opening and checking a named mapping, before the extension uses it.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "unique.hpp"

#include <chrono>
#include <cstring>
#include <string>
#include <thread>

using sharedbox::handle;
using sharedbox::header;
using sharedbox::seconds;
using sharedbox::status;

namespace {

constexpr sharedbox::field_spec fields[2] = {{0, 8, sharedbox::kind_int}, {8, 16, sharedbox::kind_bytes}};

sharedbox::result<handle> create(const std::string &name) {
    static const std::int64_t initial = 42;
    const sharedbox::value value{0, std::as_bytes(std::span(&initial, 1))};
    return handle::create(name, fields, 32, 0x5EED, 64, {&value, 1});
}

status open_status(const std::string &name, double timeout = 1.0) {
    const auto opened = handle::open(name, seconds(timeout));
    return opened ? status::ok : opened.error();
}

header &header_of(const handle &h) { return *static_cast<header *>(h.base()); }

TEST_CASE("create open and unlink") {
    const std::string name = unique("basic");
    auto owner = create(name);
    REQUIRE(owner.has_value());
    CHECK((owner->size() == 4096 && owner->minor_version() == 0));
    CHECK(header_of(*owner).record == 1728);
    CHECK(create(name).error() == status::exists);
    auto other = handle::open(name, seconds(1.0));
    CHECK(other.has_value());
    if (other) {
        CHECK((other->schema_hash() == 0x5EED && other->field_count() == 2));
        CHECK((other->create_id() == owner->create_id() && owner->create_id() != 0));
        CHECK((other->field(1).offset == 8 && other->field(1).capacity == 16));
        std::int64_t stored = 0;
        std::memcpy(&stored, static_cast<const std::byte *>(other->base()) + 1728, sizeof stored);
        CHECK(stored == 42);
    }
    CHECK(sharedbox::unlink(name).has_value());
#ifndef _WIN32
    CHECK(open_status(name, 0.1) == status::not_found);
    CHECK(sharedbox::unlink(name).error() == status::not_found);
#endif
}

TEST_CASE("creator is recorded") {
    const std::string name = unique("creator");
    std::uint64_t first_id = 0;
    {
        auto owner = create(name);
        REQUIRE(owner.has_value());
        const header &h = header_of(*owner);
        CHECK(h.creator_pid == sharedbox::detail::current_pid());
        CHECK(h.creator_start == sharedbox::detail::current_start());
        CHECK(h.creator_pidns == sharedbox::detail::current_pidns());
        const auto seen = sharedbox::inspect(name);
        CHECK((seen && seen->creator_pid == h.creator_pid && seen->create_id == owner->create_id()));
        first_id = owner->create_id();
        static_cast<void>(sharedbox::unlink(name));
    }
    // A box made again under the name gets another id.
    auto second = create(name);
    CHECK((second && second->create_id() != first_id));
    static_cast<void>(sharedbox::unlink(name));
}

// Every geometry check of open, each against a header forged in place, then the version checks.
TEST_CASE("checks on open") {
    const std::string name = unique("checks");
    auto owner = create(name);
    REQUIRE(owner.has_value());
    header &h = header_of(*owner);
    const auto forged = [&](auto &field, auto bad) {
        const auto saved = field;
        field = bad;
        const status rc = open_status(name);
        field = saved;
        return rc;
    };
    CHECK(forged(h.tail, 136u) == status::corrupt);
    // Not 64-byte aligned.
    CHECK(forged(h.record, 1736u) == status::corrupt);
    // Aligned, but the record starts where the mapping ends.
    CHECK(forged(h.record, 4096u) == status::corrupt);
    // 64-byte aligned, but inside the waiter slots, which end at 1696.
    CHECK(forged(h.record, 1664u) == status::corrupt);
    CHECK(forged(h.record_size, 4096u - 1728u + 1u) == status::corrupt);
    CHECK(forged(h.size, 8192u) == status::corrupt);
    CHECK(forged(h.field_count, std::uint16_t{0}) == status::corrupt);
    CHECK(forged(h.waiter_slots, std::uint16_t{0}) == status::corrupt);
    auto *table = reinterpret_cast<sharedbox::stored_field *>(static_cast<std::byte *>(owner->base()) + 128);
    // The bytes field moved to 16: aligned and inside the record, but its 20 bytes end past 32.
    CHECK(forged(table[1].offset, 16u) == status::corrupt);
    // Moved to 4: aligned for bytes, but it overlaps the int field.
    CHECK(forged(table[1].offset, 4u) == status::corrupt);
    // Kind 5 is unknown; a capacity of 0 is refused.
    CHECK(forged(table[1].capacity_and_kind, 16u | 5u << 24) == status::corrupt);
    CHECK(forged(table[1].capacity_and_kind, 0u | 4u << 24) == status::corrupt);
    CHECK(open_status(name) == status::ok);
    h.layout_minor = 7;
    auto newer = handle::open(name, seconds(1.0));
    CHECK((newer && newer->minor_version() == 0));
    CHECK(forged(h.layout_major, std::uint16_t{2}) == status::layout);
    const auto seen = sharedbox::inspect(name);
    CHECK((seen && seen->layout_minor == 7));
    h.layout_minor = 0;
    static_cast<void>(sharedbox::unlink(name));
}

// The upper limits, each against a header whose mapping is large enough that nothing else refuses it.
TEST_CASE("geometry limits") {
    constexpr std::uint32_t mapped = std::uint32_t{1} << 30;
    header h{};
    h.field_count = 1;
    h.waiter_slots = 1;
    h.tail = sharedbox::header_size;
    h.size = mapped;
    h.record = std::uint32_t{1} << 20;
    h.record_size = 64;
    const auto with = [&](auto &field, auto bad) {
        const auto saved = field;
        field = bad;
        const status rc = sharedbox::detail::check_geometry(h, mapped);
        field = saved;
        return rc;
    };
    CHECK(sharedbox::detail::check_geometry(h, mapped) == status::ok);
    CHECK(with(h.field_count, std::uint16_t{256}) == status::ok);
    CHECK(with(h.field_count, std::uint16_t{257}) == status::corrupt);
    CHECK(with(h.waiter_slots, std::uint16_t{4096}) == status::ok);
    CHECK(with(h.waiter_slots, std::uint16_t{4097}) == status::corrupt);
    CHECK(with(h.record_size, sharedbox::detail::max_record_size) == status::ok);
    CHECK(with(h.record_size, sharedbox::detail::max_record_size + 1u) == status::corrupt);
}

TEST_CASE("names") {
    std::string longest = unique("names");
    longest.resize(sharedbox::name_max, 'n');
    {
        auto owner = create(longest);
        CHECK(owner.has_value());
        static_cast<void>(sharedbox::unlink(longest));
    }
    CHECK(create(longest + "n").error() == status::range);
    CHECK(create("bad/name").error() == status::range);
    CHECK(create("").error() == status::range);
    CHECK(open_status("bad name") == status::range);
}

// Shared memory of other software under the name: create refuses it, open waits for a magic that never
// comes and reports not_found after the timeout, and inspect finds no header.
TEST_CASE("a foreign mapping is not a box") {
    const std::string name = unique("foreign");
#ifdef _WIN32
    const auto wide = sharedbox::detail::make_wide_name(name, "");
    HANDLE foreign = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, 4096, wide.data());
    CHECK(foreign != nullptr);
#else
    const auto path = sharedbox::detail::make_name("/", name, "");
    const int foreign = shm_open(path.data(), O_CREAT | O_EXCL | O_RDWR, 0600);
    CHECK((foreign >= 0 && ftruncate(foreign, 4096) == 0));
#endif
    CHECK(create(name).error() == status::exists);
    const auto start = std::chrono::steady_clock::now();
    CHECK(open_status(name, 0.2) == status::not_found);
    const double waited = seconds(std::chrono::steady_clock::now() - start).count();
    CHECK(waited >= 0.15);
    CHECK(sharedbox::inspect(name).error() == status::not_found);
#ifdef _WIN32
    CloseHandle(foreign);
#else
    close(foreign);
    shm_unlink(path.data());
#endif
}

#ifndef _WIN32
// A creator that has made the name but not yet sized it: open waits for the size, then gives up.
TEST_CASE("an unsized mapping is waited for") {
    const std::string name = unique("unsized");
    const auto path = sharedbox::detail::make_name("/", name, "");
    const int foreign = shm_open(path.data(), O_CREAT | O_EXCL | O_RDWR, 0600);
    CHECK(foreign >= 0);
    const auto start = std::chrono::steady_clock::now();
    CHECK(open_status(name, 0.2) == status::not_found);
    const double waited = seconds(std::chrono::steady_clock::now() - start).count();
    CHECK(waited >= 0.15);
    close(foreign);
    shm_unlink(path.data());
}
#endif

TEST_CASE("create refuses bad fields") {
    const std::string name = unique("fields");
    constexpr sharedbox::field_spec overlapping[2] = {{0, 8, sharedbox::kind_int}, {4, 4, sharedbox::kind_bytes}};
    CHECK(handle::create(name, overlapping, 16, 1, 64, {}).error() == status::range);
    constexpr sharedbox::field_spec past_end[1] = {{8, 8, sharedbox::kind_int}};
    CHECK(handle::create(name, past_end, 8, 1, 64, {}).error() == status::range);
    CHECK(handle::create(name, fields, 32, 1, 0, {}).error() == status::range);
    CHECK(handle::create(name, fields, 32, 1, 4097, {}).error() == status::range);
    const std::byte seven[7] = {};
    const sharedbox::value short_int{0, seven};
    CHECK(handle::create(name, fields, 32, 1, 64, {&short_int, 1}).error() == status::range);
    CHECK(open_status(name, 0.1) == status::not_found);
}

// open waits for publish(); create does both steps at once, so only create_unpublished shows the wait.
TEST_CASE("create_unpublished is opened only after publish") {
    const std::string name = unique("unpublished");
    static const std::int64_t initial = 42;
    const sharedbox::value value{0, std::as_bytes(std::span(&initial, 1))};
    auto owner = handle::create_unpublished(name, fields, 32, 0x5EED, 64, {&value, 1});
    REQUIRE(owner.has_value());
    CHECK(open_status(name, 0.1) == status::not_found);
    CHECK(sharedbox::inspect(name).error() == status::not_found);
    CHECK(create(name).error() == status::exists);
    status seen = status::os;
    std::thread opener([&] { seen = open_status(name, 5.0); });
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    CHECK(owner->publish().has_value());
    opener.join();
    CHECK(seen == status::ok);
    CHECK(owner->publish().error() == status::range);
    auto other = handle::open(name, seconds(1.0));
    REQUIRE(other.has_value());
    CHECK(other->publish().error() == status::range);
    std::int64_t stored = 0;
    std::memcpy(&stored, static_cast<const std::byte *>(other->base()) + 1728, sizeof stored);
    CHECK(stored == 42);
    static_cast<void>(sharedbox::unlink(name));
}

} // namespace
