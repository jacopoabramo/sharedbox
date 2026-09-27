// The atomic operations the protocols use, on the header's own fields, under contention. Each test
// fails if a field is reached through something other than a lock-free atomic of its full width, or
// with a weaker ordering than the protocol names.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include <array>
#include <atomic>
#include <cstdint>
#include <memory>
#include <thread>
#include <vector>

namespace {

constexpr int threads = 4;
// The sanitizer build of CI passes a smaller count; the store-buffering test is slow under ASan.
#ifdef SHAREDBOX_TEST_ROUNDS
constexpr int rounds = SHAREDBOX_TEST_ROUNDS;
#else
constexpr int rounds = 100000;
#endif
constexpr std::uint32_t total = threads * rounds;

using sharedbox::detail::atomic;

template <class F> void run_threads(int count, F f) {
    std::vector<std::thread> pool;
    for (int i = 0; i < count; ++i)
        pool.emplace_back(f, i);
    for (auto &t : pool)
        t.join();
}

// fetch_add: every thread records the values it got back. Each old value from the start up to the
// final count must come back exactly once: a lost update repeats one, and an operation that returns
// the new value, or truncates the high word, shifts them all.
TEST_CASE("fetch add returns each old value once") {
    auto h = std::make_unique<sharedbox::header>();
    constexpr std::uint32_t start32 = UINT32_MAX - 10;
    constexpr std::uint64_t start64 = std::uint64_t{1} << 40;
    h->waiters = start32;
    h->seq = start64;
    std::vector<std::uint32_t> got32(total);
    std::vector<std::uint64_t> got64(total);
    run_threads(threads, [&](int t) {
        for (int i = 0; i < rounds; ++i) {
            got32[t * rounds + i] = atomic(h->waiters).fetch_add(1, std::memory_order_relaxed);
            got64[t * rounds + i] = atomic(h->seq).fetch_add(1, std::memory_order_acq_rel);
        }
    });
    CHECK(h->waiters == static_cast<std::uint32_t>(start32 + total));
    CHECK(h->seq == start64 + total);
    std::vector<unsigned char> seen32(total), seen64(total);
    int repeats = 0;
    for (std::uint32_t i = 0; i < total; ++i) {
        const std::uint32_t k32 = got32[i] - start32;
        const std::uint64_t k64 = got64[i] - start64;
        if (k32 >= total || seen32[k32]++)
            ++repeats;
        if (k64 >= total || seen64[k64]++)
            ++repeats;
    }
    CHECK(repeats == 0);
}

// compare_exchange: a loop of swaps must lose no update, and a failed swap must change nothing.
TEST_CASE("compare exchange loses no update") {
    auto h = std::make_unique<sharedbox::header>();
    h->seq = std::uint64_t{1} << 40;
    run_threads(threads, [&](int) {
        for (int i = 0; i < rounds; ++i) {
            std::uint32_t old32 = atomic(h->wake_word).load(std::memory_order_relaxed);
            while (!atomic(h->wake_word)
                        .compare_exchange_weak(old32, old32 + 1, std::memory_order_acq_rel,
                                               std::memory_order_relaxed)) {
            }
            std::uint64_t old64 = atomic(h->seq).load(std::memory_order_relaxed);
            while (!atomic(h->seq).compare_exchange_weak(old64, old64 + 1, std::memory_order_acq_rel,
                                                         std::memory_order_relaxed)) {
            }
        }
    });
    CHECK(h->wake_word == total);
    CHECK(h->seq == (std::uint64_t{1} << 40) + total);
}

// load and store: a writer flips seq and writer_pid between all-zero and all-one bits; a reader must
// never see a mix, which an access made of two narrower halves produces.
TEST_CASE("loads and stores do not tear") {
    auto h = std::make_unique<sharedbox::header>();
    std::atomic<int> torn{0};
    run_threads(2, [&](int t) {
        for (int i = 0; i < 4 * rounds; ++i) {
            if (t == 0) {
                atomic(h->seq).store((i & 1) ? UINT64_MAX : 0, std::memory_order_release);
                atomic(h->writer_pid).store((i & 1) ? UINT32_MAX : 0, std::memory_order_relaxed);
            } else {
                const std::uint64_t a = atomic(h->seq).load(std::memory_order_acquire);
                const std::uint32_t b = atomic(h->writer_pid).load(std::memory_order_relaxed);
                if ((a != 0 && a != UINT64_MAX) || (b != 0 && b != UINT32_MAX))
                    ++torn;
            }
        }
    });
    CHECK(torn == 0);
}

// Store buffering: thread 0 stores to wake_word and loads waiters, thread 1 the other way round. With
// seq_cst stores and loads, or a seq_cst fence between them, at least one thread sees the other's
// store; this is what keeps a writer from missing a waiter that registers at the same moment. x86
// lets a plain store wait in the store buffer past a later load, so a release store or an
// acquire-release fence shows both loads reading 0.
void store_buffering(bool fenced) {
    auto h = std::make_unique<sharedbox::header>();
    std::atomic<std::uint32_t> arrived{0};
    std::vector<std::array<std::uint32_t, 2>> seen(rounds);
    const auto barrier = [&](std::uint32_t target) {
        arrived.fetch_add(1, std::memory_order_seq_cst);
        while (arrived.load(std::memory_order_acquire) < target) {
        }
    };
    run_threads(2, [&](int t) {
        std::uint32_t &mine = t == 0 ? h->wake_word : h->waiters;
        std::uint32_t &theirs = t == 0 ? h->waiters : h->wake_word;
        for (int i = 0; i < rounds; ++i) {
            if (t == 0) {
                atomic(h->wake_word).store(0, std::memory_order_relaxed);
                atomic(h->waiters).store(0, std::memory_order_relaxed);
            }
            barrier(4 * static_cast<std::uint32_t>(i) + 2);
            if (fenced) {
                atomic(mine).store(1, std::memory_order_relaxed);
                std::atomic_thread_fence(std::memory_order_seq_cst);
                seen[i][t] = atomic(theirs).load(std::memory_order_relaxed);
            } else {
                atomic(mine).store(1, std::memory_order_seq_cst);
                seen[i][t] = atomic(theirs).load(std::memory_order_seq_cst);
            }
            barrier(4 * static_cast<std::uint32_t>(i) + 4);
        }
    });
    int both_missed = 0;
    for (const auto &pair : seen)
        if (pair[0] == 0 && pair[1] == 0)
            ++both_missed;
    CHECK(both_missed == 0);
}

} // namespace

TEST_CASE("a seq_cst store is not passed by a later load") { store_buffering(false); }

TEST_CASE("a seq_cst fence is not passed by a later load") { store_buffering(true); }
