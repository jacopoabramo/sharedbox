// Freeing the waiter slot of an exited process: both sides must know their pid namespace, a slot
// claimed again since it was read is left to its new owner, and an owner that died inside a wait leaves
// the sleepers count as it was before that wait.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "unique.hpp"

#include <chrono>
#include <cstddef>
#include <thread>

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
    sharedbox::detail::waiter_table view;
    view.claimed = &waiters(*h);
    sharedbox::waiter_slot &s = slot_0(*h);
    const std::uint32_t new_owner = sharedbox::detail::current_pid();
    s.owner_start = 12345;
    s.owner_pidns = sharedbox::detail::current_pidns();
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

TEST_CASE("a claim made while a dead owner's slot is being freed survives the rest of that free") {
    constexpr sharedbox::field_spec fields[1] = {{0, 8, sharedbox::kind_int}};
    // Above INT_MAX on Linux and not a multiple of 4 on Windows: no process has this pid.
    constexpr std::uint32_t dead = 0xFFFFFFF1u;
    const std::string name = unique("slots-free");
    auto h = handle::create(name, fields, 8, 1, 4, {});
    REQUIRE(h.has_value());
    sharedbox::waiter_slot &s = slot_0(*h);
    sharedbox::detail::waiter_table other;
    other.slots = &s;
    other.claimed = &waiters(*h);
    other.count = h->waiter_slots();
    s.owner_start = 12345;
    s.owner_pidns = sharedbox::detail::current_pidns();
    s.owner_pid = dead;
    atomic(waiters(*h)).fetch_add(1);

    // After each store of the first free, a second process scans for dead slots, and a claimer takes
    // slot 0 as soon as it is free.
    bool claimed = false;
    sharedbox::detail::free_dead_slot(other, s, dead, 12345, [&]() noexcept {
        sharedbox::detail::free_dead_waiters(other);
        if (!claimed && s.owner_pid == 0) {
            const auto slot = h->register_waiter();
            CHECK((slot && *slot == 0));
            claimed = true;
        }
    });
    CHECK(claimed);
    CHECK(h->waiter_held(0));
    CHECK(h->waiters() == 1);
    h->release_waiter(0);
    static_cast<void>(sharedbox::unlink(name));
}

namespace {

// Above INT_MAX on Linux and not a multiple of 4 on Windows: no process has this pid.
constexpr std::uint32_t no_process = 0xFFFFFFF1u;

// Makes slot 0 look claimed by an exited process killed inside a wait that added 1 to the sleepers count
// and named offset in asleep_on.
void fake_dead_sleeper(const handle &h, std::uint32_t offset) {
    sharedbox::waiter_slot &s = slot_0(h);
    s.owner_start = 12345;
    s.owner_pidns = sharedbox::detail::current_pidns();
    s.owner_pid = no_process;
    atomic(waiters(h)).fetch_add(1);
    atomic(static_cast<sharedbox::header *>(h.base())->sleepers).fetch_add(1);
    s.asleep_on = offset;
}

} // namespace

TEST_CASE("freeing the slot of an owner killed inside a wait takes it out of the sleepers count") {
    constexpr sharedbox::field_spec fields[1] = {{0, 8, sharedbox::kind_int}};
    const std::string name = unique("slots-sleeper");
    auto h = handle::create(name, fields, 8, 1, 4, {});
    REQUIRE(h.has_value());
    fake_dead_sleeper(*h, offsetof(sharedbox::header, sleepers));
    REQUIRE(h->sleepers() == 1);

    const auto slot = h->register_waiter();
    REQUIRE(slot.has_value());
    CHECK(*slot == 0);
    CHECK(h->sleepers() == 0);
    CHECK(slot_0(*h).asleep_on == 0);
    CHECK(h->waiters() == 1);
    h->release_waiter(*slot);
    static_cast<void>(sharedbox::unlink(name));
}

TEST_CASE("freeing a dead owner's slot that names no sleepers count changes no count") {
    constexpr sharedbox::field_spec fields[1] = {{0, 8, sharedbox::kind_int}};
    const std::string name = unique("slots-corrupt-sleeper");
    auto h = handle::create(name, fields, 8, 1, 4, {});
    REQUIRE(h.has_value());
    // The offset of waiters: a real count, but not one a wait adds to.
    fake_dead_sleeper(*h, offsetof(sharedbox::header, waiters));

    const auto slot = h->register_waiter();
    REQUIRE(slot.has_value());
    CHECK(*slot == 0);
    CHECK(slot_0(*h).asleep_on == 0);
    CHECK(h->waiters() == 1);
    CHECK(h->sleepers() == 1);
    h->release_waiter(*slot);
    atomic(static_cast<sharedbox::header *>(h->base())->sleepers).fetch_sub(1);
    static_cast<void>(sharedbox::unlink(name));
}

#ifndef _WIN32

TEST_CASE("a process killed while one of its threads waits leaves a slot whose freeing restores sleepers") {
    constexpr sharedbox::field_spec fields[1] = {{0, 8, sharedbox::kind_int}};
    const std::string name = unique("slots-killed-sleeper");
    auto h = handle::create(name, fields, 8, 1, 4, {});
    REQUIRE(h.has_value());
    const pid_t child = fork();
    REQUIRE(child >= 0);
    if (child == 0) {
        auto mine = handle::open(name, sharedbox::seconds(1.0));
        if (!mine)
            _exit(1);
        const auto slot = mine->register_waiter();
        if (!slot)
            _exit(2);
        std::thread([&] {
            static_cast<void>(mine->wait(*slot, mine->generation(), sharedbox::seconds(10.0)));
        }).detach();
        const auto limit = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (mine->sleepers() != 1)
            if (std::chrono::steady_clock::now() > limit)
                _exit(3);
            else
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
        // Leaves without destructors, as a killed process would, while the other thread still waits.
        _exit(0);
    }
    int code = 0;
    REQUIRE(waitpid(child, &code, 0) == child);
    REQUIRE((WIFEXITED(code) && WEXITSTATUS(code) == 0));
    CHECK(h->sleepers() == 1);

    const auto slot = h->register_waiter();
    REQUIRE(slot.has_value());
    CHECK(h->sleepers() == 0);
    CHECK(h->waiters() == 1);
    h->release_waiter(*slot);
    static_cast<void>(sharedbox::unlink(name));
}

TEST_CASE("a dead owner's slot is freed only when both namespaces are known and equal") {
    constexpr sharedbox::field_spec fields[1] = {{0, 8, sharedbox::kind_int}};
    const std::uint64_t own = sharedbox::detail::current_pidns();
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
    CHECK(sharedbox::detail::current_pidns() == 0);
    fake_dead_owner(*h, 0);
    CHECK(!registering_frees_slot_0(*h));
    fake_dead_owner(*h, own);
    CHECK(!registering_frees_slot_0(*h));
    sharedbox::detail::cache().pidns.store(0);

    CHECK(h->waiters() == 0);
    static_cast<void>(sharedbox::unlink(name));
}
#endif

TEST_CASE("a handle from a capsule frees its waiter slot before it releases the capsule") {
    constexpr sharedbox::field_spec fields[1] = {{0, 8, sharedbox::kind_int}};
    const std::string name = unique("slots-capsule");
    auto made = handle::create(name, fields, 8, 1, 4, {});
    REQUIRE(made.has_value());
    auto watcher = handle::open(name, sharedbox::seconds(1.0));
    REQUIRE(watcher.has_value());
    sbx_handle *capsule = std::move(*made).to_capsule();
    REQUIRE(capsule != nullptr);
    {
        auto taken = handle::from_capsule(capsule);
        REQUIRE(taken.has_value());
        REQUIRE(taken->register_waiter().has_value());
        CHECK(watcher->waiters() == 1);
    }
    // Freed in a mapping already released, the slot would crash the test or stay counted.
    CHECK(watcher->waiters() == 0);
    delete capsule;
    static_cast<void>(sharedbox::unlink(name));
}
