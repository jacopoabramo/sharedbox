// A stream handed over through an sbx_handle: duplicate, to_capsule and from_capsule.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "table_builder.hpp"
#include "unique.hpp"

#include <cstdint>
#include <cstring>
#include <string>

using namespace sharedbox;

namespace {

constexpr field_spec box_fields[1] = {{0, 8, kind_int}};

stream make_stream(const std::string &name) {
    auto made = stream::create(name, {}, entry_of(kind_int, 8), 4, 2, 1);
    REQUIRE(made);
    return std::move(*made);
}

std::int64_t receive_one(stream &s) {
    auto reader = s.reader(read_mode::lossless, start_at::oldest);
    REQUIRE(reader);
    std::int64_t value = 0;
    REQUIRE(reader->receive(std::as_writable_bytes(std::span{&value, 1}), seconds(1.0)));
    return value;
}

void send_one(stream &s, std::int64_t value) {
    auto sender = s.sender();
    REQUIRE(sender);
    REQUIRE(sender->send(std::as_bytes(std::span{&value, 1}), seconds(1.0)));
}

} // namespace

TEST_CASE("a duplicate stream receives what the original sends") {
    const std::string name = unique("scap-dup");
    stream original = make_stream(name);
    auto copy = original.duplicate();
    REQUIRE(copy);
    CHECK(copy->base() != original.base());
    send_one(original, 7);
    CHECK(receive_one(*copy) == 7);
    static_cast<void>(unlink(name));
}

TEST_CASE("duplicate checks the header it maps again") {
    const std::string name = unique("scap-forged");
    stream original = make_stream(name);
    auto &common = static_cast<common_header *>(original.base())[0];
    const std::uint16_t slots = common.waiter_slots;
    common.waiter_slots = static_cast<std::uint16_t>(max_waiter_slots + 1);
    auto copy = original.duplicate();
    REQUIRE_FALSE(copy);
    CHECK(copy.error().code == status::corrupt);
    common.waiter_slots = slots;
    static_cast<void>(unlink(name));
}

TEST_CASE("a stream of a newer minor reports this header's minor") {
    const std::string name = unique("scap-minor");
    stream original = make_stream(name);
    static_cast<common_header *>(original.base())->kind_minor = stream_layout_minor + 1;
    CHECK(original.major_version() == stream_layout_major);
    CHECK(original.minor_version() == stream_layout_minor);
    auto copy = original.duplicate();
    REQUIRE(copy);
    CHECK(copy->minor_version() == stream_layout_minor);
    sbx_handle *h = std::move(*copy).to_capsule();
    REQUIRE(h != nullptr);
    CHECK(h->layout_major == stream_layout_major);
    CHECK(h->layout_minor == stream_layout_minor);
    h->release(h);
    delete h;
    static_cast<void>(unlink(name));
}

TEST_CASE("a stream survives a round trip through a capsule") {
    const std::string name = unique("scap-trip");
    stream original = make_stream(name);
    sbx_handle *h = nullptr;
    {
        auto copy = original.duplicate();
        REQUIRE(copy);
        h = std::move(*copy).to_capsule();
    }
    REQUIRE(h != nullptr);
    CHECK(h->handle_version == 1);
    CHECK(h->layout_major == stream_layout_major);
    CHECK(h->layout_minor == stream_layout_minor);
    CHECK(std::string(h->name) == name);
    {
        auto taken = stream::from_capsule(h);
        REQUIRE(taken);
        CHECK(h->release == nullptr);
        send_one(original, 11);
        CHECK(receive_one(*taken) == 11);
    }
    delete h;
    static_cast<void>(unlink(name));
}

TEST_CASE("a stream from a capsule cannot be duplicated") {
    const std::string name = unique("scap-nodup");
    stream original = make_stream(name);
    auto copy = original.duplicate();
    REQUIRE(copy);
    sbx_handle *h = std::move(*copy).to_capsule();
    REQUIRE(h != nullptr);
    {
        auto taken = stream::from_capsule(h);
        REQUIRE(taken);
        auto again = taken->duplicate();
        REQUIRE_FALSE(again);
        CHECK(again.error().code == status::range);
    }
    delete h;
    static_cast<void>(unlink(name));
}

TEST_CASE("a box handle is not a stream") {
    const std::string name = unique("scap-box");
    auto box = handle::create(name, box_fields, 8, 1, 1, {});
    REQUIRE(box);
    auto copy = box->duplicate();
    REQUIRE(copy);
    sbx_handle *h = std::move(*copy).to_capsule();
    REQUIRE(h != nullptr);
    auto taken = stream::from_capsule(h);
    REQUIRE_FALSE(taken);
    CHECK(taken.error().code == status::kind_mismatch);
    CHECK(h->release != nullptr);
    h->release(h);
    delete h;
    static_cast<void>(unlink(name));
}

TEST_CASE("from_capsule refuses a malformed handle") {
    CHECK(stream::from_capsule(nullptr).error().code == status::range);
    const std::string name = unique("scap-bad");
    stream original = make_stream(name);
    auto copy = original.duplicate();
    REQUIRE(copy);
    sbx_handle *h = std::move(*copy).to_capsule();
    REQUIRE(h != nullptr);
    sbx_handle released = *h;
    released.release = nullptr;
    CHECK(stream::from_capsule(&released).error().code == status::range);
    h->release(h);
    delete h;
    static_cast<void>(unlink(name));
}
