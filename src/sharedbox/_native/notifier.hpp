#pragma once

#include <atomic>
#include <cstdint>
#include <string>

namespace sharedbox {

/// Blocks threads of any process until a shared 32-bit word changes.
class Notifier {
public:
    Notifier(const std::string &segment_name, std::atomic<std::uint32_t> &word,
             std::atomic<std::uint32_t> &waiters);
    ~Notifier();
    Notifier(const Notifier &) = delete;
    Notifier &operator=(const Notifier &) = delete;

    /// Changes the word and wakes every waiter.
    void wake_all();
    /// Returns once the word differs from expected or timeout seconds pass; may return early.
    void wait(std::uint32_t expected, double timeout);

private:
    std::atomic<std::uint32_t> &word_;
    std::atomic<std::uint32_t> &waiters_;
#ifdef _WIN32
    void *semaphore_ = nullptr;
#endif
};

} // namespace sharedbox
