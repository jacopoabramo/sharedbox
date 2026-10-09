// Layout 3.0: the common first line every kind shares, the box's line 1 after it, and refusing what
// this build cannot read.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "unique.hpp"

#include <cstring>
#include <string>

using namespace sharedbox;

namespace {

static_assert(sizeof(common_header) == 64 && alignof(common_header) == 8);
static_assert(sizeof(header) == 128 && header_size == 128);

constexpr field_spec fields[2] = {{0, 8, kind_int}, {8, 16, kind_bytes}};

header &header_of(const handle &h) { return *static_cast<header *>(h.base()); }

status open_status(const std::string &name) {
    const auto opened = handle::open(name, seconds(0.2));
    return opened ? status::ok : opened.error().code;
}

} // namespace

TEST_CASE("a new box is core 1.0, box layout 3.0") {
    const std::string name = unique("layout3");
    auto owner = handle::create(name, fields, 32, 0x5EED, 4, {});
    REQUIRE(owner);
    const header &h = header_of(*owner);
    CHECK(h.common.magic == box_magic);
    CHECK(h.common.core_major == core_major);
    CHECK(h.common.kind_major == 3);
    CHECK(h.tail == header_size);
    CHECK(owner->major_version() == 3);
    auto other = handle::open(name, seconds(1.0));
    REQUIRE(other);
    CHECK(other->create_id() == owner->create_id());
    static_cast<void>(unlink(name));
}

TEST_CASE("a core major this build does not read is refused with the version found") {
    const std::string name = unique("core2");
    auto owner = handle::create(name, fields, 32, 1, 4, {});
    REQUIRE(owner);
    header_of(*owner).common.core_major = 2;
    auto other = handle::open(name, seconds(0.2));
    REQUIRE_FALSE(other);
    CHECK(other.error().code == status::layout);
    CHECK(other.error().found == (std::uint64_t{2} << 16));
    static_cast<void>(unlink(name));
}

TEST_CASE("a box layout major this build does not read is refused with the version found") {
    const std::string name = unique("box4");
    auto owner = handle::create(name, fields, 32, 1, 4, {});
    REQUIRE(owner);
    header_of(*owner).common.kind_major = 4;
    auto other = handle::open(name, seconds(0.2));
    REQUIRE_FALSE(other);
    CHECK(other.error().code == status::layout);
    CHECK(other.error().found == (std::uint64_t{4} << 16));
    static_cast<void>(unlink(name));
}

TEST_CASE("another kind is a kind mismatch and an unknown magic is a layout error") {
    const std::string name = unique("magic");
    auto owner = handle::create(name, fields, 32, 1, 4, {});
    REQUIRE(owner);
    header_of(*owner).common.magic = stream_magic;
    auto other = handle::open(name, seconds(0.2));
    REQUIRE_FALSE(other);
    CHECK(other.error().code == status::kind_mismatch);
    CHECK(other.error().found == stream_magic);
    constexpr std::uint64_t old_box = 0x3158424445524853;
    header_of(*owner).common.magic = old_box;
    other = handle::open(name, seconds(0.2));
    REQUIRE_FALSE(other);
    CHECK(other.error().code == status::foreign);
    CHECK(other.error().found == old_box);
    static_cast<void>(unlink(name));
}

TEST_CASE("from_capsule refuses what open refuses") {
    const std::string name = unique("capsule-refused");
    auto owner = handle::create(name, fields, 32, 1, 4, {});
    REQUIRE(owner);
    header &h = header_of(*owner);
    const auto taken_status = [&] {
        auto copy = owner->duplicate();
        REQUIRE(copy);
        sbx_handle *capsule = std::move(*copy).to_capsule();
        REQUIRE(capsule != nullptr);
        auto taken = handle::from_capsule(capsule);
        const status rc = taken ? status::ok : taken.error().code;
        if (!taken)
            capsule->release(capsule);
        delete capsule;
        return rc;
    };
    CHECK(taken_status() == status::ok);
    h.common.magic = 0x3158424445524853;
    CHECK(taken_status() == status::foreign);
    h.common.magic = 0x1234;
    CHECK(taken_status() == status::foreign);
    h.common.magic = stream_magic;
    CHECK(taken_status() == status::kind_mismatch);
    h.common.magic = box_magic;
    h.common.kind_major = 4;
    CHECK(taken_status() == status::layout);
    h.common.kind_major = 3;
    CHECK(taken_status() == status::ok);
    static_cast<void>(unlink(name));
}

TEST_CASE("the box geometry checks") {
    const std::string name = unique("geometry");
    auto owner = handle::create(name, fields, 32, 1, 4, {});
    REQUIRE(owner);
    header &h = header_of(*owner);
    h.types_size = 4;
    CHECK(open_status(name) == status::corrupt);
    h.types_size = 64;
    CHECK(open_status(name) == status::corrupt);
    h.types_size = 0;
    h.common.size += 4096;
    CHECK(open_status(name) == status::corrupt);
    h.common.size -= 4096;
    CHECK(open_status(name) == status::ok);
    static_cast<void>(unlink(name));
}

TEST_CASE("the record limit") {
    header h{};
    h.common.waiter_slots = 1;
    h.field_count = 1;
    h.tail = header_size;
    h.record = 192;
    h.record_size = static_cast<std::uint32_t>(max_mapping_size - 192);
    CHECK(detail::box_layout::check_geometry(h, max_mapping_size) == status::ok);
    CHECK(detail::box_layout::check_geometry(h, max_mapping_size + page_size) == status::corrupt);
    h.record_size += 8;
    CHECK(detail::box_layout::check_geometry(h, max_mapping_size) == status::corrupt);
}

TEST_CASE("kinds 6 to 12 are fixed kinds with their sizes") {
    using namespace sharedbox;
    CHECK(detail::fixed_capacity(kind_complex) == 16);
    CHECK(detail::fixed_capacity(kind_date) == 4);
    CHECK(detail::fixed_capacity(kind_time) == 16);
    CHECK(detail::fixed_capacity(kind_datetime) == 16);
    CHECK(detail::fixed_capacity(kind_timedelta) == 12);
    CHECK(detail::fixed_capacity(kind_uuid) == 16);
    CHECK(detail::prefixed(kind_decimal));
    CHECK(detail::kind_alignment(kind_datetime) == 8);
    CHECK(detail::kind_alignment(kind_timedelta) == 4);
    CHECK(detail::kind_alignment(kind_uuid) == 1);
    CHECK(detail::kind_known(kind_array));
    CHECK_FALSE(detail::kind_known(13));
    CHECK_FALSE(detail::kind_known(75));
    const std::string name = unique("l3-kinds");
    const field_spec typed[3] = {{0, 16, kind_datetime}, {16, 12, kind_timedelta}, {28, 10, kind_decimal}};
    CHECK(handle::create(name, typed, 48, 1, 4, {}).has_value());
    static_cast<void>(unlink(name));
}

TEST_CASE("a record that, with the tail before it, passes the mapping limit is refused") {
    const std::string name = unique("l3-big");
    const auto made =
        handle::create(name, fields, static_cast<std::uint32_t>(sharedbox::max_mapping_size - 8), 1, 64, {});
    REQUIRE_FALSE(made.has_value());
    CHECK(made.error().code == status::range);
    CHECK(open_status(name) == status::not_found);
}
