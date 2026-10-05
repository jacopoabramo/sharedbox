// Layout 2.0: the header field it adds, the checks per major version, and opening a 1.0 box.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "unique.hpp"

#include <string>

using sharedbox::handle;
using sharedbox::header;
using sharedbox::seconds;
using sharedbox::status;

namespace {

constexpr sharedbox::field_spec fields[2] = {{0, 8, sharedbox::kind_int}, {8, 16, sharedbox::kind_bytes}};

header &header_of(const handle &h) { return *static_cast<header *>(h.base()); }

status open_status(const std::string &name) {
    const auto opened = handle::open(name, seconds(0.2));
    return opened ? status::ok : opened.error();
}

} // namespace

TEST_CASE("a new box is layout 2.0 with an empty description table") {
    const std::string name = unique("v2-new");
    auto owner = handle::create(name, fields, 32, 0x5EED, 64, {});
    REQUIRE(owner.has_value());
    CHECK(header_of(*owner).layout_major == 2);
    CHECK(header_of(*owner).types_size == 0);
    CHECK(owner->major_version() == 2);
    auto other = handle::open(name, seconds(1.0));
    REQUIRE(other.has_value());
    CHECK(other->major_version() == 2);
    static_cast<void>(sharedbox::unlink(name));
}

TEST_CASE("a 1.0 box opens and its capsule says 1") {
    const std::string name = unique("v2-old");
    auto owner = sharedbox::detail::create_impl(name, fields, 32, 0x5EED, 64, {}, {}, 1, true);
    REQUIRE(owner.has_value());
    CHECK(header_of(*owner).layout_major == 1);
    auto other = handle::open(name, seconds(1.0));
    REQUIRE(other.has_value());
    CHECK(other->major_version() == 1);
    sbx_handle *capsule = std::move(*other).to_capsule();
    REQUIRE(capsule != nullptr);
    CHECK(capsule->layout_major == 1);
    capsule->release(capsule);
    delete capsule;
    static_cast<void>(sharedbox::unlink(name));
}

TEST_CASE("the checks follow the segment's major version") {
    const std::string name = unique("v2-forged");
    auto owner = handle::create(name, fields, 32, 0x5EED, 64, {});
    REQUIRE(owner.has_value());
    header &h = header_of(*owner);
    h.layout_major = 3;
    CHECK(open_status(name) == status::layout);
    h.layout_major = 0;
    CHECK(open_status(name) == status::layout);
    h.layout_major = 1;
    h.types_size = 8;
    CHECK(open_status(name) == status::corrupt);
    h.layout_major = 2;
    // Not a multiple of 8.
    h.types_size = 4;
    CHECK(open_status(name) == status::corrupt);
    // The table would run into the record, which starts right after the slots.
    h.types_size = 64;
    CHECK(open_status(name) == status::corrupt);
    h.types_size = 0;
    CHECK(open_status(name) == status::ok);
    static_cast<void>(sharedbox::unlink(name));
}

TEST_CASE("the 2.0 record limit") {
    header h{};
    h.layout_major = 2;
    h.field_count = 1;
    h.waiter_slots = 1;
    h.tail = sharedbox::header_size;
    h.record = 192;
    h.record_size = static_cast<std::uint32_t>(sharedbox::max_mapping_size - 192);
    h.size = static_cast<std::uint32_t>(sharedbox::max_mapping_size);
    CHECK(sharedbox::detail::check_geometry(h, sharedbox::max_mapping_size) == status::ok);
    h.record_size += 8;
    CHECK(sharedbox::detail::check_geometry(h, sharedbox::max_mapping_size) == status::corrupt);
    // 1.0 keeps its own, lower limit.
    h.layout_major = 1;
    h.record_size = sharedbox::detail::max_record_size + 8;
    CHECK(sharedbox::detail::check_geometry(h, sharedbox::max_mapping_size) == status::corrupt);
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
    const std::string name = unique("v2-kinds");
    const field_spec typed[3] = {{0, 16, kind_datetime}, {16, 12, kind_timedelta}, {28, 10, kind_decimal}};
    CHECK(handle::create(name, typed, 48, 1, 4, {}).has_value());
    static_cast<void>(unlink(name));
}

TEST_CASE("a record that, with the tail before it, passes the mapping limit is refused") {
    const std::string name = unique("v2-big");
    const auto made =
        handle::create(name, fields, static_cast<std::uint32_t>(sharedbox::max_mapping_size - 8), 1, 64, {});
    REQUIRE_FALSE(made.has_value());
    CHECK(made.error() == status::range);
    CHECK(open_status(name) == status::not_found);
}
