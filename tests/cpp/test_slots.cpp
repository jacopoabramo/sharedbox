// Freeing the waiter slot of an exited process depends on both sides knowing their pid namespace.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "unique.hpp"

#include <sys/wait.h>
#include <unistd.h>

using sharedbox::handle;
using sharedbox::detail::atomic;

namespace {

// The pid of a child that has exited and been reaped.
std::uint32_t dead_pid() {
    const pid_t child = fork();
    if (child == 0)
        _exit(0);
    waitpid(child, nullptr, 0);
    return static_cast<std::uint32_t>(child);
}

sharedbox::waiter_slot &slot_0(const handle &h) {
    auto *base = static_cast<std::byte *>(h.base());
    return *reinterpret_cast<sharedbox::waiter_slot *>(base + 128 + 16 * h.field_count());
}

std::uint32_t &waiters(const handle &h) { return static_cast<sharedbox::header *>(h.base())->waiters; }

// Makes slot 0 look claimed by an exited process that recorded pidns.
void fake_dead_owner(const handle &h, std::uint64_t pidns) {
    sharedbox::waiter_slot &s = slot_0(h);
    s.owner_start = 12345;
    s.owner_pidns = pidns;
    s.owner_pid = dead_pid();
    atomic(waiters(h)).fetch_add(1);
}

// Registers a waiter, which first frees dead slots, and says whether slot 0 was freed.
bool registering_frees_slot_0(handle &h) {
    const auto slot = h.register_waiter();
    CHECK(slot.has_value());
    const bool freed = slot && *slot == 0;
    if (slot)
        h.release_waiter(*slot);
    sharedbox::waiter_slot &s = slot_0(h);
    if (s.owner_pid != 0) {
        s.owner_pid = 0;
        s.owner_start = 0;
        atomic(waiters(h)).fetch_sub(1);
    }
    return freed;
}

} // namespace

TEST_CASE("a dead owner's slot is freed only when both namespaces are known and equal") {
    constexpr sharedbox::field_spec fields[1] = {{0, 8, sharedbox::kind_int}};
    const std::uint64_t own = sharedbox::current_pidns();
    const std::string name = unique("slots");
    CHECK(own != 0);
    auto h = handle::create(name, fields, 8, 1, 4, {});
    REQUIRE(h.has_value());

    fake_dead_owner(*h, own);
    CHECK(registering_frees_slot_0(*h));

    fake_dead_owner(*h, own + 1);
    CHECK(!registering_frees_slot_0(*h));

    fake_dead_owner(*h, 0);
    CHECK(!registering_frees_slot_0(*h));

    // This process's own namespace unknown, as without /proc: the cache holds the namespace plus one.
    sharedbox::detail::cache().pidns.store(1);
    CHECK(sharedbox::current_pidns() == 0);
    fake_dead_owner(*h, 0);
    CHECK(!registering_frees_slot_0(*h));
    fake_dead_owner(*h, own);
    CHECK(!registering_frees_slot_0(*h));
    sharedbox::detail::cache().pidns.store(0);

    CHECK(h->waiters() == 0);
    static_cast<void>(sharedbox::unlink(name));
}
