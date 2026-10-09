// Waiting in a waiter slot: a write wakes it, an interrupt ends only that slot's wait, and the call refuses a
// slot the handle does not hold.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "unique.hpp"

#include <chrono>
#include <cstdint>
#include <optional>
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
    CHECK((!quiet && quiet.error().code == status::timeout));
    h.release_waiter(*slot);
    static_cast<void>(sharedbox::unlink(name));
}

TEST_CASE("wait refuses a slot not held and a timeout out of range") {
    const std::string name = unique("wait-range");
    handle h = make(name);
    const auto unheld = h.wait(1, h.generation(), 0.01s);
    CHECK((!unheld && unheld.error().code == status::range));
    const auto slot = h.register_waiter();
    REQUIRE(slot.has_value());
    const auto negative = h.wait(*slot, h.generation(), -1.0s);
    CHECK((!negative && negative.error().code == status::range));
    CHECK(h.interrupt(4).error().code == status::range);
    h.release_waiter(*slot);
    static_cast<void>(sharedbox::unlink(name));
}

namespace {

std::uint32_t waiters_at_release = 0;
int releases = 0;

void count_release(sbx_handle *capsule) {
    waiters_at_release =
        sharedbox::detail::atomic(static_cast<sharedbox::header *>(capsule->base)->waiters).load();
    ++releases;
    capsule->release = nullptr;
}

} // namespace

TEST_CASE("a handle taken from a capsule frees only its own slots and events, then releases the capsule") {
    const std::string name = unique("wait-capsule");
    handle h = make(name);
    const auto mine = h.register_waiter();
    REQUIRE(mine.has_value());
    sbx_handle producer{sharedbox::layout_major,
                        h.minor_version(),
                        sharedbox::handle_version,
                        h.base(),
                        h.size(),
                        h.name().data(),
                        count_release,
                        nullptr};
    {
        auto foreign = handle::from_capsule(&producer);
        REQUIRE(foreign.has_value());
        CHECK(producer.release == nullptr);
        CHECK(foreign->duplicate().error().code == status::range);
        const auto theirs = foreign->register_waiter();
        REQUIRE(theirs.has_value());
        CHECK(h.waiters() == 2);
        REQUIRE(foreign->interrupt(*mine).has_value());
        const auto woken = h.wait(*mine, h.generation(), 1.0s);
        CHECK((woken && *woken == wake::interrupted));
        CHECK(releases == 0);
    }
    CHECK(releases == 1);
    CHECK(waiters_at_release == 1);
    CHECK(h.waiter_held(*mine));
    REQUIRE(h.interrupt(*mine).has_value());
    const auto again = h.wait(*mine, h.generation(), 1.0s);
    CHECK((again && *again == wake::interrupted));
    h.release_waiter(*mine);
    static_cast<void>(sharedbox::unlink(name));
}

TEST_CASE("a duplicate keeps its own mapping through a capsule after the original is gone") {
    const std::string name = unique("wait-duplicate");
    std::optional<handle> h(make(name));
    auto copy = h->duplicate();
    REQUIRE(copy.has_value());
    CHECK(copy->base() != h->base());
    sbx_handle *capsule = std::move(*copy).to_capsule();
    REQUIRE(capsule != nullptr);
    h.reset();
    static_cast<void>(sharedbox::unlink(name));
    {
        auto taken = handle::from_capsule(capsule);
        REQUIRE(taken.has_value());
        write_zero(*taken);
        CHECK(taken->version(0) == 1);
    }
    CHECK(capsule->release == nullptr);
    delete capsule;
}
