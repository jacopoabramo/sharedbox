// The ring: every mode against one sender, the gate, waits and their wake-ups, and closing.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "table_builder.hpp"
#include "unique.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cstring>
#include <string>
#include <thread>

#ifndef _WIN32
#include <sys/wait.h>
#include <unistd.h>
#endif

using namespace sharedbox;
using namespace std::chrono_literals;

namespace {

// The sanitizer build of CI passes a smaller count.
#ifdef SHAREDBOX_TEST_ROUNDS
constexpr std::uint64_t rounds = SHAREDBOX_TEST_ROUNDS;
#else
constexpr std::uint64_t rounds = 100000;
#endif

// Items of 16 u64 that all hold the item's position, so a torn copy shows as two different values.
constexpr std::uint64_t words = 16;
using item = std::array<std::uint64_t, words>;

table_builder words_array() {
    table_builder t;
    t.head(kind_array, 0, words * 8);
    t.put(dl_dtype{1, 64, 1});
    t.put(std::uint8_t{1});
    t.put(std::uint8_t{0});
    t.put(std::uint16_t{0});
    t.put(words);
    t.pad();
    return t;
}

stream make(const std::string &name, std::uint64_t capacity, std::uint32_t readers = 4) {
    const table_builder t = words_array();
    auto made = stream::create(name, t.span(), entry_of(kind_array, 0), capacity, readers, 1);
    REQUIRE(made);
    return std::move(*made);
}

result<std::uint64_t> send(stream_sender &s, std::uint64_t value, double timeout = 5.0) {
    item it;
    it.fill(value);
    return s.send(std::as_bytes(std::span{it}), seconds(timeout));
}

bool whole(const item &it, std::uint64_t position) {
    for (const std::uint64_t w : it)
        if (w != position)
            return false;
    return true;
}

// Receives one item; a torn one counts as a failure.
result<received> receive(stream_reader &r, double timeout = 5.0) {
    item it{};
    result<received> got = r.receive(std::as_writable_bytes(std::span{it}), seconds(timeout));
    if (got && !whole(it, got->position))
        return sharedbox::unexpected(status::corrupt);
    return got;
}

using clock = std::chrono::steady_clock;

} // namespace

TEST_CASE("a lossless reader receives every position once and in order") {
    const std::string name = unique("stream-lossless");
    stream st = make(name, 4);
    auto reader = st.reader(read_mode::lossless, start_at::oldest);
    auto sender = st.sender();
    REQUIRE((reader && sender));
    std::uint64_t bad = 0;
    std::thread consumer([&] {
        for (std::uint64_t i = 0; i < rounds; ++i) {
            const auto got = receive(*reader);
            if (!got || got->position != i || got->missed != 0)
                ++bad;
        }
    });
    std::uint64_t failed = 0;
    for (std::uint64_t i = 0; i < rounds; ++i)
        if (!send(*sender, i))
            ++failed;
    consumer.join();
    CHECK(failed == 0);
    CHECK(bad == 0);
    static_cast<void>(unlink(name));
}

TEST_CASE("lossy and latest readers never return a torn item, and receive plus miss everything sent") {
    for (const read_mode mode : {read_mode::lossy, read_mode::latest}) {
        CAPTURE(static_cast<int>(mode));
        const std::string name = unique("stream-lossy");
        stream st = make(name, 4);
        auto reader = st.reader(mode, start_at::oldest);
        auto sender = st.sender();
        REQUIRE((reader && sender));
        std::uint64_t received_count = 0;
        std::uint64_t backwards = 0;
        status last_error = status::ok;
        std::thread consumer([&] {
            std::uint64_t next = 0;
            for (;;) {
                const auto got = receive(*reader);
                if (!got) {
                    last_error = got.error().code;
                    break;
                }
                if (got->position < next)
                    ++backwards;
                next = got->position + 1;
                ++received_count;
            }
        });
        std::uint64_t failed = 0;
        for (std::uint64_t i = 0; i < rounds; ++i)
            if (!send(*sender, i))
                ++failed;
        sender->close();
        consumer.join();
        CHECK(failed == 0);
        CHECK(last_error == status::ended);
        CHECK(backwards == 0);
        CHECK(received_count + reader->missed() == rounds);
        static_cast<void>(unlink(name));
    }
}

TEST_CASE("a lossy reader lapped several times resumes at the oldest item in the ring and counts the rest") {
    const std::string name = unique("stream-lapped-lossy");
    stream st = make(name, 4);
    auto reader = st.reader(read_mode::lossy, start_at::oldest);
    auto sender = st.sender();
    REQUIRE((reader && sender));
    for (std::uint64_t i = 0; i < 10; ++i)
        REQUIRE(send(*sender, i));
    for (std::uint64_t expected = 6; expected < 10; ++expected) {
        const auto got = receive(*reader);
        REQUIRE(got);
        CHECK(got->position == expected);
        CHECK(got->missed == (expected == 6 ? 6 : 0));
    }
    CHECK(reader->missed() == 6);
    const auto empty = receive(*reader, 0.0);
    CHECK((!empty && empty.error().code == status::timeout));
    REQUIRE(send(*sender, 10));
    const auto next = receive(*reader);
    REQUIRE(next);
    CHECK(next->position == 10);
    CHECK(next->missed == 0);
    static_cast<void>(unlink(name));
}

TEST_CASE("a latest reader lapped several times returns the newest item and counts the rest") {
    const std::string name = unique("stream-lapped-latest");
    stream st = make(name, 4);
    auto reader = st.reader(read_mode::latest, start_at::oldest);
    auto sender = st.sender();
    REQUIRE((reader && sender));
    for (std::uint64_t i = 0; i < 10; ++i)
        REQUIRE(send(*sender, i));
    const auto got = receive(*reader);
    REQUIRE(got);
    CHECK(got->position == 9);
    CHECK(got->missed == 9);
    const auto empty = receive(*reader, 0.0);
    CHECK((!empty && empty.error().code == status::timeout));
    for (std::uint64_t i = 10; i < 13; ++i)
        REQUIRE(send(*sender, i));
    const auto newest = receive(*reader);
    REQUIRE(newest);
    CHECK(newest->position == 12);
    CHECK(newest->missed == 2);
    static_cast<void>(unlink(name));
}

TEST_CASE("a lossless reader that keeps pace with the sender over many laps loses nothing") {
    const std::string name = unique("stream-laps-lossless");
    stream st = make(name, 4);
    auto reader = st.reader(read_mode::lossless, start_at::oldest);
    auto sender = st.sender();
    REQUIRE((reader && sender));
    std::uint64_t position = 0;
    for (std::uint64_t lap = 0; lap < 50; ++lap) {
        for (std::uint64_t i = 0; i < 4; ++i)
            REQUIRE(send(*sender, lap * 4 + i));
        for (std::uint64_t i = 0; i < 4; ++i) {
            const auto got = receive(*reader);
            REQUIRE(got);
            CHECK(got->position == position);
            CHECK(got->missed == 0);
            ++position;
        }
    }
    static_cast<void>(unlink(name));
}

TEST_CASE("an item overwritten while it is copied is discarded and counted as missed") {
    const std::string name = unique("stream-lapped");
    stream st = make(name, 2);
    auto reader = st.reader(read_mode::lossy, start_at::oldest);
    auto sender = st.sender();
    REQUIRE((reader && sender));
    REQUIRE(send(*sender, 0));
    bool lapped = false;
    item it{};
    const auto got = reader->receive_with(
        [&](std::span<const std::byte> bytes) noexcept {
            const std::size_t half = bytes.size() / 2;
            std::memcpy(it.data(), bytes.data(), half);
            if (!lapped) {
                lapped = true;
                // Positions 1 and 2: position 2 reuses the slot being copied.
                static_cast<void>(send(*sender, 1, 0.0));
                static_cast<void>(send(*sender, 2, 0.0));
            }
            std::memcpy(reinterpret_cast<std::byte *>(it.data()) + half, bytes.data() + half, bytes.size() - half);
        },
        seconds(1.0));
    REQUIRE(got);
    CHECK(got->position == 1);
    CHECK(got->missed == 1);
    CHECK(whole(it, 1));
    static_cast<void>(unlink(name));
}

TEST_CASE("a lossless reader that joins while the sender runs misses nothing after its first item") {
    const std::string name = unique("stream-join");
    stream st = make(name, 4);
    auto sender = st.sender();
    REQUIRE(sender);
    std::atomic<bool> stop{false};
    std::uint64_t failed = 0;
    std::thread producer([&] {
        for (std::uint64_t i = 0; !stop.load(); ++i)
            if (!send(*sender, i))
                ++failed;
    });
    std::uint64_t gaps = 0;
    for (std::uint64_t join = 0; join < rounds / 1000; ++join) {
        auto reader = st.reader(read_mode::lossless, start_at::oldest);
        if (!reader) {
            ++gaps;
            continue;
        }
        std::uint64_t previous = 0;
        for (int k = 0; k < 50; ++k) {
            const auto got = receive(*reader);
            if (!got || (k > 0 && got->position != previous + 1) || got->missed != 0)
                ++gaps;
            if (got)
                previous = got->position;
        }
        reader->close();
    }
    stop = true;
    producer.join();
    CHECK(failed == 0);
    CHECK(gaps == 0);
    static_cast<void>(unlink(name));
}

TEST_CASE("a stopped lossless reader blocks the sender once the ring is full, lossy and latest never do") {
    const std::string name = unique("stream-full");
    stream st = make(name, 4);
    auto lossy = st.reader(read_mode::lossy, start_at::newest);
    auto latest = st.reader(read_mode::latest, start_at::newest);
    auto sender = st.sender();
    REQUIRE((lossy && latest && sender));
    for (std::uint64_t i = 0; i < 100; ++i)
        CHECK(send(*sender, i, 0.0));
    // Starts at 99, the newest, which the ring still holds; 100 to 102 fit beside it.
    auto lossless = st.reader(read_mode::lossless, start_at::newest);
    REQUIRE(lossless);
    for (std::uint64_t i = 100; i < 103; ++i)
        CHECK(send(*sender, i, 0.0));
    const auto start = clock::now();
    const auto blocked = send(*sender, 103, 0.0);
    REQUIRE_FALSE(blocked);
    CHECK(blocked.error().code == status::timeout);
    CHECK(clock::now() - start < 50ms);
    const auto first = receive(*lossless);
    REQUIRE(first);
    CHECK(first->position == 99);
    CHECK(send(*sender, 103, 0.0));
    static_cast<void>(unlink(name));
}

TEST_CASE("a waiting receive wakes on send, and after close a reader gets what is buffered, then ended") {
    const std::string name = unique("stream-close");
    stream st = make(name, 4);
    auto reader = st.reader(read_mode::lossless, start_at::newest);
    auto sender = st.sender();
    REQUIRE((reader && sender));
    result<received> got = sharedbox::unexpected(status::ok);
    std::thread waiter([&] { got = receive(*reader); });
    std::this_thread::sleep_for(100ms);
    const auto start = clock::now();
    const auto sent = send(*sender, 0);
    waiter.join();
    CHECK(sent);
    CHECK(clock::now() - start < 1s);
    CHECK((got && got->position == 0));

    CHECK(send(*sender, 1));
    CHECK(send(*sender, 2));
    sender->close();
    CHECK(st.ended());
    CHECK(st.sender().error().code == status::ended);
    auto late = st.reader(read_mode::lossy, start_at::oldest);
    REQUIRE(late);
    for (std::uint64_t expected = 1; expected < 3; ++expected) {
        const auto next = receive(*reader);
        CHECK((next && next->position == expected));
    }
    for (std::uint64_t expected = 0; expected < 3; ++expected) {
        const auto next = receive(*late);
        CHECK((next && next->position == expected));
    }
    const auto drained = clock::now();
    CHECK(receive(*reader).error().code == status::ended);
    CHECK(receive(*late).error().code == status::ended);
    CHECK(clock::now() - drained < 1s);
    static_cast<void>(unlink(name));
}

TEST_CASE("a waiting send wakes when the lossless reader advances, and interrupt ends a wait") {
    const std::string name = unique("stream-space");
    stream st = make(name, 2);
    auto reader = st.reader(read_mode::lossless, start_at::newest);
    auto sender = st.sender();
    REQUIRE((reader && sender));
    CHECK(send(*sender, 0));
    CHECK(send(*sender, 1));
    result<std::uint64_t> sent = sharedbox::unexpected(status::ok);
    std::thread blocked([&] { sent = send(*sender, 2); });
    std::this_thread::sleep_for(100ms);
    auto start = clock::now();
    const auto first = receive(*reader);
    blocked.join();
    CHECK(first);
    CHECK(clock::now() - start < 1s);
    CHECK((sent && *sent == 2));

    CHECK(receive(*reader));
    CHECK(receive(*reader));
    result<received> got = sharedbox::unexpected(status::ok);
    std::thread waiting([&] { got = receive(*reader); });
    std::this_thread::sleep_for(100ms);
    start = clock::now();
    CHECK(reader->interrupt());
    waiting.join();
    CHECK(clock::now() - start < 1s);
    CHECK(got.error().code == status::interrupted);

    CHECK(send(*sender, 3));
    CHECK(send(*sender, 4));
    std::thread full([&] { sent = send(*sender, 5); });
    std::this_thread::sleep_for(100ms);
    CHECK(sender->interrupt());
    full.join();
    CHECK(sent.error().code == status::interrupted);
    static_cast<void>(unlink(name));
}

TEST_CASE("a second sender is busy, and a refused reader gives back its waiter slot") {
    const std::string name = unique("stream-slots");
    stream st = make(name, 4, 2);
    auto a = st.reader(read_mode::lossy, start_at::newest);
    auto b = st.reader(read_mode::lossless, start_at::newest);
    REQUIRE((a && b));
    // Each refused call claims the third waiter slot before it finds no entry.
    for (int i = 0; i < 3; ++i)
        CHECK(st.reader(read_mode::lossy, start_at::newest).error().code == status::no_slot);
    reader_info info[4];
    CHECK(st.readers(info) == 2);
    auto first = st.sender();
    REQUIRE(first);
    CHECK(st.sender_pid() == detail::current_pid());
    // Closing a frees a waiter slot, so the second sender() reaches the claim of sender_pid.
    a->close();
    CHECK(st.sender().error().code == status::busy);
    CHECK(st.reader(read_mode::latest, start_at::newest));
    static_cast<void>(unlink(name));
}

TEST_CASE("a send with no timeout keeps a pending interrupt for the next send") {
    const std::string name = unique("stream-nowait-interrupt");
    stream st = make(name, 2);
    auto reader = st.reader(read_mode::lossless, start_at::newest);
    auto sender = st.sender();
    REQUIRE((reader && sender));
    CHECK(send(*sender, 0));
    CHECK(send(*sender, 1));
    CHECK(sender->interrupt());
    const auto nowait = send(*sender, 2, 0.0);
    REQUIRE_FALSE(nowait);
    CHECK(nowait.error().code == status::timeout);
    const auto waited = send(*sender, 2, 1.0);
    REQUIRE_FALSE(waited);
    CHECK(waited.error().code == status::interrupted);
    static_cast<void>(unlink(name));
}

TEST_CASE("newest starts at the last item sent and oldest at the oldest still in the ring") {
    const std::string name = unique("stream-start");
    stream st = make(name, 4);
    auto empty = st.reader(read_mode::lossy, start_at::newest);
    REQUIRE(empty);
    const auto start = clock::now();
    CHECK(receive(*empty, 0.0).error().code == status::timeout);
    CHECK(clock::now() - start < 50ms);
    auto sender = st.sender();
    REQUIRE(sender);
    for (std::uint64_t i = 0; i < 3; ++i)
        CHECK(send(*sender, i));
    const auto first_of = [&](start_at where) {
        auto r = st.reader(read_mode::lossy, where);
        REQUIRE(r);
        const auto got = receive(*r);
        REQUIRE(got);
        return got->position;
    };
    CHECK(first_of(start_at::newest) == 2);
    CHECK(first_of(start_at::oldest) == 0);
    for (std::uint64_t i = 3; i < 10; ++i)
        CHECK(send(*sender, i));
    CHECK(first_of(start_at::newest) == 9);
    CHECK(first_of(start_at::oldest) == 6);
    static_cast<void>(unlink(name));
}

TEST_CASE("data_waiting and space_waiting are back to 0 after a wait that was woken, timed out or interrupted") {
    const std::string name = unique("stream-counts");
    stream st = make(name, 2);
    stream_header &h = *static_cast<stream_header *>(st.base());
    const auto waiting = [&] {
        return std::pair{detail::atomic(h.data_waiting).load(), detail::atomic(h.space_waiting).load()};
    };
    auto reader = st.reader(read_mode::lossless, start_at::newest);
    auto sender = st.sender();
    REQUIRE((reader && sender));

    CHECK(receive(*reader, 0.05).error().code == status::timeout);
    CHECK(waiting() == std::pair<std::uint32_t, std::uint32_t>{0, 0});

    std::thread woken([&] { static_cast<void>(receive(*reader)); });
    CHECK(send(*sender, 0));
    woken.join();
    CHECK(waiting() == std::pair<std::uint32_t, std::uint32_t>{0, 0});

    result<received> got = sharedbox::unexpected(status::ok);
    std::thread waiting_reader([&] { got = receive(*reader); });
    CHECK(reader->interrupt());
    waiting_reader.join();
    CHECK(got.error().code == status::interrupted);
    CHECK(waiting() == std::pair<std::uint32_t, std::uint32_t>{0, 0});

    CHECK(send(*sender, 1));
    CHECK(send(*sender, 2));
    CHECK(send(*sender, 3, 0.05).error().code == status::timeout);
    CHECK(waiting() == std::pair<std::uint32_t, std::uint32_t>{0, 0});

    std::thread blocked([&] { static_cast<void>(send(*sender, 3)); });
    CHECK(receive(*reader));
    blocked.join();
    CHECK(waiting() == std::pair<std::uint32_t, std::uint32_t>{0, 0});

    CHECK(receive(*reader));
    CHECK(receive(*reader));
    CHECK(send(*sender, 4));
    CHECK(send(*sender, 5));
    result<std::uint64_t> sent = sharedbox::unexpected(status::ok);
    std::thread full([&] { sent = send(*sender, 6); });
    CHECK(sender->interrupt());
    full.join();
    CHECK(waiting() == std::pair<std::uint32_t, std::uint32_t>{0, 0});
    static_cast<void>(unlink(name));
}

TEST_CASE("freeing the waiter slot of a reader killed inside a wait for data takes it out of data_waiting") {
    const std::string name = unique("stream-dead-sleeper");
    stream st = make(name, 2);
    stream_header &h = *static_cast<stream_header *>(st.base());
    auto *base = static_cast<std::byte *>(st.base());
    waiter_slot &s = *reinterpret_cast<waiter_slot *>(base + h.readers + h.max_readers * sizeof(reader_entry));
    // Above INT_MAX on Linux and not a multiple of 4 on Windows: no process has this pid.
    s.owner_start = 12345;
    s.owner_pidns = detail::current_pidns();
    s.owner_pid = 0xFFFFFFF1u;
    detail::atomic(h.waiters).fetch_add(1);
    detail::atomic(h.data_waiting).fetch_add(1);
    s.asleep_on = offsetof(stream_header, data_waiting);

    auto reader = st.reader(read_mode::lossless, start_at::newest);
    REQUIRE(reader);
    CHECK(detail::atomic(h.data_waiting).load() == 0);
    CHECK(detail::atomic(h.space_waiting).load() == 0);
    CHECK(s.asleep_on == 0);
    CHECK(detail::atomic(h.waiters).load() == 1);
    static_cast<void>(unlink(name));
}

TEST_CASE("freeing the waiter slot of a sender killed inside a wait for space takes it out of space_waiting") {
    const std::string name = unique("stream-dead-space-sleeper");
    stream st = make(name, 2);
    stream_header &h = *static_cast<stream_header *>(st.base());
    auto *base = static_cast<std::byte *>(st.base());
    waiter_slot &s = *reinterpret_cast<waiter_slot *>(base + h.readers + h.max_readers * sizeof(reader_entry));
    // Above INT_MAX on Linux and not a multiple of 4 on Windows: no process has this pid.
    s.owner_start = 12345;
    s.owner_pidns = detail::current_pidns();
    s.owner_pid = 0xFFFFFFF1u;
    detail::atomic(h.waiters).fetch_add(1);
    detail::atomic(h.space_waiting).fetch_add(1);
    s.asleep_on = offsetof(stream_header, space_waiting);

    auto reader = st.reader(read_mode::lossless, start_at::newest);
    REQUIRE(reader);
    CHECK(detail::atomic(h.space_waiting).load() == 0);
    CHECK(detail::atomic(h.data_waiting).load() == 0);
    CHECK(s.asleep_on == 0);
    CHECK(detail::atomic(h.waiters).load() == 1);
    static_cast<void>(unlink(name));
}

#ifndef _WIN32

namespace {

// Runs body in a child process, which leaves with _exit as a killed process would, without destructors,
// and returns its exit code.
template <class F> int in_child(F body) {
    const pid_t child = fork();
    if (child < 0)
        return -1;
    if (child == 0)
        _exit(body());
    int code = 0;
    if (waitpid(child, &code, 0) != child)
        return -1;
    return WIFEXITED(code) ? WEXITSTATUS(code) : -1;
}

} // namespace

TEST_CASE("a lossless reader whose process died stops blocking the sender") {
    const std::string name = unique("stream-dead-reader");
    stream st = make(name, 4);
    auto sender = st.sender();
    REQUIRE(sender);
    REQUIRE(in_child([&] {
                auto mine = stream::open(name, seconds(1.0));
                if (!mine)
                    _exit(1);
                auto reader = mine->reader(read_mode::lossless, start_at::newest);
                _exit(reader ? 0 : 2);
                return 3;
            }) == 0);
    for (std::uint64_t i = 0; i < 4; ++i)
        CHECK(send(*sender, i, 0.0));
    const auto start = clock::now();
    CHECK(send(*sender, 4));
    CHECK(clock::now() - start < 2s);
    CHECK(st.readers({}) == 0);
    static_cast<void>(unlink(name));
}

TEST_CASE("a sender whose process died ends the stream once what it sent is read") {
    const std::string name = unique("stream-dead-sender");
    stream st = make(name, 4);
    auto reader = st.reader(read_mode::lossless, start_at::newest);
    REQUIRE(reader);
    REQUIRE(in_child([&] {
                auto mine = stream::open(name, seconds(1.0));
                if (!mine)
                    _exit(1);
                auto sender = mine->sender();
                if (!sender || !send(*sender, 0) || !send(*sender, 1))
                    _exit(2);
                _exit(0);
                return 3;
            }) == 0);
    for (std::uint64_t expected = 0; expected < 2; ++expected) {
        const auto got = receive(*reader);
        CHECK((got && got->position == expected));
    }
    const auto start = clock::now();
    CHECK(receive(*reader).error().code == status::ended);
    CHECK(clock::now() - start < 2s);
    static_cast<void>(unlink(name));
}

TEST_CASE("a sender whose process died is replaced") {
    const std::string name = unique("stream-replace");
    stream st = make(name, 4);
    REQUIRE(in_child([&] {
                auto mine = stream::open(name, seconds(1.0));
                if (!mine)
                    _exit(1);
                auto sender = mine->sender();
                _exit(sender ? 0 : 2);
                return 3;
            }) == 0);
    auto sender = st.sender();
    CHECK(sender);
    CHECK_FALSE(st.ended());
    static_cast<void>(unlink(name));
}

TEST_CASE("a reader inherited across fork is refused in the child, which leaves its entry alone") {
    const std::string name = unique("stream-fork");
    stream st = make(name, 4);
    auto reader = st.reader(read_mode::lossless, start_at::newest);
    auto sender = st.sender();
    REQUIRE((reader && sender));
    CHECK(send(*sender, 0));
    REQUIRE(in_child([&] {
                item it{};
                const auto got = reader->receive(std::as_writable_bytes(std::span{it}), seconds(0.0));
                if (got || got.error().code != status::range)
                    _exit(1);
                reader->close();
                _exit(0);
                return 2;
            }) == 0);
    reader_info info[1];
    CHECK(st.readers(info) == 1);
    const auto got = receive(*reader);
    CHECK((got && got->position == 0));
    static_cast<void>(unlink(name));
}

#endif

namespace {

// Makes the sender of st look like one whose process has exited.
void forge_dead_sender(stream &st) {
    auto &h = *static_cast<stream_header *>(st.base());
    detail::atomic(h.sender_pidns).store(detail::current_pidns());
    detail::atomic(h.sender_start).store(detail::current_start() + 1);
    detail::atomic(h.sender_pid).store(detail::current_pid());
}

} // namespace

TEST_CASE("a dead sender claimed by one path is left alone by the other") {
    const std::string name = unique("stream-claim-dead");
    stream st = make(name, 4);
    auto reader = st.reader(read_mode::lossless, start_at::newest);
    REQUIRE(reader);
    forge_dead_sender(st);
    auto &h = *static_cast<stream_header *>(st.base());
    const std::uint32_t dead = detail::current_pid();
    SUBCASE("a reader waiting while the replacement holds the claim does not end the stream") {
        std::uint64_t start = 0;
        bool backed_off = false;
        REQUIRE(detail::claim_dead_sender(h, dead, start, [&]() noexcept {
            backed_off = receive(*reader, 0.3).error().code == status::timeout;
        }));
        CHECK(backed_off);
        CHECK_FALSE(st.ended());
    }
    SUBCASE("a replacement while a claim is in progress gives busy") {
        detail::atomic(h.sender_start).store(detail::start_freeing);
        CHECK(st.sender().error().code == status::busy);
        CHECK_FALSE(st.ended());
    }
    SUBCASE("a reader waiting with a dead sender ends the stream and leaves no sender") {
        CHECK(receive(*reader, 2.0).error().code == status::ended);
        CHECK(st.ended());
        CHECK(st.sender_pid() == 0);
        CHECK(st.sender().error().code == status::ended);
    }
    SUBCASE("a replacement of a dead sender leaves the stream open") {
        auto sender = st.sender();
        REQUIRE(sender);
        CHECK_FALSE(st.ended());
        CHECK(st.sender_pid() == dead);
        CHECK(send(*sender, 0));
        CHECK(receive(*reader, 0.0));
    }
    static_cast<void>(unlink(name));
}

namespace {

// Moves write_pos back to 3 after positions 0 to 3 were sent, the state between the sender's seq store and
// its write_pos store for position 3.
void rewind_write_pos(stream &st) {
    auto &h = *static_cast<stream_header *>(st.base());
    detail::atomic(h.write_pos).store(3);
}

} // namespace

TEST_CASE("a lossless reader one past write_pos waits instead of finding corruption") {
    const std::string name = unique("stream-past-write-pos");
    stream st = make(name, 4);
    auto reader = st.reader(read_mode::lossless, start_at::oldest);
    REQUIRE(reader);
    auto sender = st.sender();
    REQUIRE(sender);
    for (std::uint64_t i = 0; i < 4; ++i)
        REQUIRE(send(*sender, i));
    rewind_write_pos(st);
    for (std::uint64_t expected = 0; expected < 4; ++expected) {
        const auto got = receive(*reader);
        REQUIRE((got && got->position == expected));
    }
    CHECK(receive(*reader, 0.0).error().code == status::timeout);
    static_cast<void>(unlink(name));
}

TEST_CASE("a lossy reader one past write_pos neither repeats nor misses an item") {
    const std::string name = unique("stream-past-write-pos-lossy");
    stream st = make(name, 4);
    auto reader = st.reader(read_mode::lossy, start_at::oldest);
    REQUIRE(reader);
    auto sender = st.sender();
    REQUIRE(sender);
    for (std::uint64_t i = 0; i < 4; ++i)
        REQUIRE(send(*sender, i));
    rewind_write_pos(st);
    for (std::uint64_t expected = 0; expected < 4; ++expected) {
        const auto got = receive(*reader);
        REQUIRE((got && got->position == expected && got->missed == 0));
    }
    CHECK(receive(*reader, 0.0).error().code == status::timeout);
    detail::atomic(static_cast<stream_header *>(st.base())->write_pos).store(4);
    REQUIRE(send(*sender, 4));
    const auto next = receive(*reader);
    CHECK((next && next->position == 4 && next->missed == 0));
    static_cast<void>(unlink(name));
}

TEST_CASE("a reader one past write_pos of an ended stream gets ended") {
    const std::string name = unique("stream-past-write-pos-ended");
    stream st = make(name, 4);
    auto reader = st.reader(read_mode::lossless, start_at::oldest);
    REQUIRE(reader);
    auto sender = st.sender();
    REQUIRE(sender);
    for (std::uint64_t i = 0; i < 4; ++i)
        REQUIRE(send(*sender, i));
    rewind_write_pos(st);
    detail::atomic(static_cast<stream_header *>(st.base())->state).store(stream_ended);
    for (std::uint64_t expected = 0; expected < 4; ++expected) {
        const auto got = receive(*reader);
        REQUIRE((got && got->position == expected));
    }
    CHECK(receive(*reader, 0.0).error().code == status::ended);
    static_cast<void>(unlink(name));
}

TEST_CASE("a sender killed before its write_pos store has its last item completed by the claimer") {
    const std::string name = unique("stream-dead-last-publish");
    stream st = make(name, 4);
    auto behind = st.reader(read_mode::lossless, start_at::oldest);
    auto at_last = st.reader(read_mode::lossless, start_at::oldest);
    REQUIRE((behind && at_last));
    auto sender = st.sender();
    REQUIRE(sender);
    for (std::uint64_t i = 0; i < 4; ++i)
        REQUIRE(send(*sender, i));
    for (std::uint64_t i = 0; i < 3; ++i) {
        const auto got = receive(*at_last);
        REQUIRE((got && got->position == i));
    }
    rewind_write_pos(st);
    forge_dead_sender(st);
    auto &h = *static_cast<stream_header *>(st.base());
    SUBCASE("ending the stream publishes the item, then readers get ended") {
        const auto last = receive(*at_last, 2.0);
        REQUIRE((last && last->position == 3));
        CHECK(receive(*at_last, 2.0).error().code == status::ended);
        CHECK(detail::atomic(h.write_pos).load() == 4);
        for (std::uint64_t expected = 0; expected < 4; ++expected) {
            const auto got = receive(*behind);
            REQUIRE((got && got->position == expected));
        }
        CHECK(receive(*behind, 2.0).error().code == status::ended);
    }
    SUBCASE("a replacement continues after the item, which readers receive once") {
        auto next = st.sender();
        REQUIRE(next);
        CHECK(detail::atomic(h.write_pos).load() == 4);
        for (std::uint64_t expected = 0; expected < 4; ++expected) {
            const auto got = receive(*behind);
            REQUIRE((got && got->position == expected));
        }
        const auto position = send(*next, 4);
        REQUIRE(position);
        CHECK(*position == 4);
        const auto fifth = receive(*behind);
        CHECK((fifth && fifth->position == 4));
        const auto last = receive(*at_last);
        REQUIRE((last && last->position == 3));
        const auto after = receive(*at_last);
        CHECK((after && after->position == 4));
    }
    static_cast<void>(unlink(name));
}
