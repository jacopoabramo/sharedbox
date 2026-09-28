// Freeing the waiter slot of an exited process: both sides must know their pid namespace, and a slot
// claimed again since it was read is left to its new owner.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "unique.hpp"

#ifndef _WIN32
#include <sys/wait.h>
#include <unistd.h>
#endif

using sharedbox::handle;
using sharedbox::detail::atomic;

namespace {

#ifndef _WIN32
// The pid of a child that has exited and been reaped.
std::uint32_t dead_pid() {
    const pid_t child = fork();
    if (child == 0)
        _exit(0);
    waitpid(child, nullptr, 0);
    return static_cast<std::uint32_t>(child);
}
#endif

sharedbox::waiter_slot &slot_0(const handle &h) {
    auto *base = static_cast<std::byte *>(h.base());
    return *reinterpret_cast<sharedbox::waiter_slot *>(base + 128 + 16 * h.field_count());
}

std::uint32_t &waiters(const handle &h) { return static_cast<sharedbox::header *>(h.base())->waiters; }

#ifndef _WIN32
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
#endif

} // namespace

TEST_CASE("a dead owner's slot claimed again before it is freed is left to the new owner") {
    constexpr sharedbox::field_spec fields[1] = {{0, 8, sharedbox::kind_int}};
    const std::string name = unique("slots-aba");
    auto h = handle::create(name, fields, 8, 1, 4, {});
    REQUIRE(h.has_value());
    sharedbox::detail::state view;
    view.hdr = static_cast<sharedbox::header *>(h->base());
    sharedbox::waiter_slot &s = slot_0(*h);
    const std::uint32_t new_owner = sharedbox::current_pid();
    s.owner_start = 12345;
    s.owner_pidns = sharedbox::current_pidns();
    s.owner_pid = new_owner;
    atomic(waiters(*h)).fetch_add(1);

    // A freer that read pid new_owner + 1 with the same start time before the slot changed hands.
    sharedbox::detail::free_dead_slot(view, s, new_owner + 1, 12345);
    CHECK(s.owner_pid == new_owner);
    CHECK(s.owner_start == 12345);
    CHECK(h->waiters() == 1);

    s.owner_pid = 0;
    s.owner_start = 0;
    atomic(waiters(*h)).fetch_sub(1);
    static_cast<void>(sharedbox::unlink(name));
}

#ifndef _WIN32

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
#endif
