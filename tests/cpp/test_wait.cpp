// Waiting in a waiter slot: a write wakes it, an interrupt ends only that slot's wait, and the call refuses a
// slot the handle does not hold.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "unique.hpp"

#include <chrono>
#include <cstdint>
#include <thread>

using namespace std::chrono_literals;
using sharedbox::handle;
using sharedbox::status;
using sharedbox::wake;

namespace {

constexpr sharedbox::field_spec fields[1] = {{0, 8, sharedbox::kind_int}};

handle make(const std::string &name) {
    auto h = handle::create(name, fields, 8, 1, 4, {});
    REQUIRE(h.has_value());
    return std::move(*h);
}

void write_zero(handle &h) {
    const std::int64_t zero = 0;
    const sharedbox::value v{0, std::as_bytes(std::span{&zero, 1})};
    REQUIRE(h.write({&v, 1}, 1.0s).has_value());
}

} // namespace

TEST_CASE("a write wakes a waiter, and an interrupt ends only its own slot's wait") {
    const std::string name = unique("wait");
    handle h = make(name);
    const auto first = h.register_waiter();
    const auto second = h.register_waiter();
    REQUIRE((first && second));

    sharedbox::result<wake> interrupted = sharedbox::unexpected(status::ok);
    sharedbox::result<wake> changed = sharedbox::unexpected(status::ok);
    std::thread a([&] { interrupted = h.wait(*first, h.generation(), 5.0s); });
    std::thread b([&] { changed = h.wait(*second, h.generation(), 5.0s); });
    std::this_thread::sleep_for(100ms);
    auto start = std::chrono::steady_clock::now();
    REQUIRE(h.interrupt(*first).has_value());
    a.join();
    CHECK((interrupted && *interrupted == wake::interrupted));
    // A lost wake still ends the wait with the right result, but only at the 5 s timeout.
    CHECK(std::chrono::steady_clock::now() - start < 1s);
    start = std::chrono::steady_clock::now();
    write_zero(h);
    b.join();
    CHECK((changed && *changed == wake::changed));
    CHECK(std::chrono::steady_clock::now() - start < 1s);

    h.release_waiter(*first);
    h.release_waiter(*second);
    static_cast<void>(sharedbox::unlink(name));
}

TEST_CASE("an interrupt sent before the wait ends it, and a quiet wait times out") {
    const std::string name = unique("wait-early");
    handle h = make(name);
    const auto slot = h.register_waiter();
    REQUIRE(slot.has_value());
    REQUIRE(h.interrupt(*slot).has_value());
    const auto early = h.wait(*slot, h.generation(), 5.0s);
    CHECK((early && *early == wake::interrupted));
    const auto quiet = h.wait(*slot, h.generation(), 0.05s);
    CHECK((!quiet && quiet.error() == status::timeout));
    h.release_waiter(*slot);
    static_cast<void>(sharedbox::unlink(name));
}

TEST_CASE("wait refuses a slot not held and a timeout out of range") {
    const std::string name = unique("wait-range");
    handle h = make(name);
    const auto unheld = h.wait(1, h.generation(), 0.01s);
    CHECK((!unheld && unheld.error() == status::range));
    const auto slot = h.register_waiter();
    REQUIRE(slot.has_value());
    const auto negative = h.wait(*slot, h.generation(), -1.0s);
    CHECK((!negative && negative.error() == status::range));
    CHECK(h.interrupt(4).error() == status::range);
    h.release_waiter(*slot);
    static_cast<void>(sharedbox::unlink(name));
}
