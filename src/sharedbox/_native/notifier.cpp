#include "notifier.hpp"

#include <climits>
#include <system_error>

#ifdef _WIN32
#include <windows.h>
#else
#include <ctime>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif

namespace sharedbox {

#ifdef _WIN32

Notifier::Notifier(const std::string &segment_name, std::atomic<std::uint32_t> &word,
                   std::atomic<std::uint32_t> &waiters)
    : word_(word), waiters_(waiters) {
    // Segment names are ASCII, so widening byte by byte is exact.
    std::wstring name = L"Local\\sharedbox." + std::wstring(segment_name.begin(), segment_name.end()) + L".wake";
    semaphore_ = CreateSemaphoreW(nullptr, 0, LONG_MAX, name.c_str());
    if (semaphore_ == nullptr)
        throw std::system_error(static_cast<int>(GetLastError()), std::system_category(), "CreateSemaphoreW");
}

Notifier::~Notifier() { CloseHandle(semaphore_); }

void Notifier::wake_all() {
    word_.fetch_add(1, std::memory_order_seq_cst);
    auto pending = static_cast<LONG>(waiters_.load(std::memory_order_seq_cst));
    if (pending > 0)
        ReleaseSemaphore(semaphore_, pending, nullptr);
}

void Notifier::wait(std::uint32_t expected, double timeout) {
    waiters_.fetch_add(1, std::memory_order_seq_cst);
    if (word_.load(std::memory_order_seq_cst) == expected) {
        DWORD ms = timeout <= 0 ? 0 : static_cast<DWORD>(timeout * 1000.0 + 0.5);
        // ponytail: another waiter can take this waiter's permit, so a wake-up may arrive
        // up to 50 ms late; a per-waiter event would remove the delay.
        WaitForSingleObject(semaphore_, ms < 50 ? ms : 50);
    }
    waiters_.fetch_sub(1, std::memory_order_seq_cst);
}

#else

static_assert(sizeof(std::atomic<std::uint32_t>) == sizeof(std::uint32_t));

Notifier::Notifier(const std::string &, std::atomic<std::uint32_t> &word, std::atomic<std::uint32_t> &waiters)
    : word_(word), waiters_(waiters) {}

Notifier::~Notifier() = default;

void Notifier::wake_all() {
    word_.fetch_add(1, std::memory_order_seq_cst);
    syscall(SYS_futex, reinterpret_cast<std::uint32_t *>(&word_), FUTEX_WAKE, INT_MAX, nullptr, nullptr, 0);
}

void Notifier::wait(std::uint32_t expected, double timeout) {
    if (timeout <= 0)
        return;
    timespec ts;
    ts.tv_sec = static_cast<time_t>(timeout);
    ts.tv_nsec = static_cast<long>((timeout - static_cast<double>(ts.tv_sec)) * 1e9);
    syscall(SYS_futex, reinterpret_cast<std::uint32_t *>(&word_), FUTEX_WAIT, expected, &ts, nullptr, 0);
}

#endif

} // namespace sharedbox
