#include "liveness.hpp"

#ifdef _WIN32
#include <windows.h>
#else
#include <atomic>
#include <cerrno>
#include <csignal>
#include <fstream>
#include <iterator>
#include <pthread.h>
#include <sstream>
#include <string>
#include <sys/types.h>
#include <unistd.h>
#endif

namespace sharedbox {

#ifdef _WIN32

std::uint64_t process_start(std::uint32_t pid) {
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (process == nullptr)
        return 0;
    FILETIME created, exited, kernel, user;
    std::uint64_t start = 0;
    DWORD code = 0;
    // A process that has exited stays queryable while any handle to it is open.
    if (GetProcessTimes(process, &created, &exited, &kernel, &user) && GetExitCodeProcess(process, &code) &&
        code == STILL_ACTIVE)
        start = (static_cast<std::uint64_t>(created.dwHighDateTime) << 32) | created.dwLowDateTime;
    CloseHandle(process);
    return start;
}

ProcessId current_process() {
    static const ProcessId self{GetCurrentProcessId(), process_start(GetCurrentProcessId())};
    return self;
}

#else

std::uint64_t process_start(std::uint32_t pid) {
    // EPERM means the process exists but belongs to another user.
    if (kill(static_cast<pid_t>(pid), 0) != 0 && errno == ESRCH)
        return 0;
    std::ifstream stat("/proc/" + std::to_string(pid) + "/stat");
    std::string text((std::istreambuf_iterator<char>(stat)), std::istreambuf_iterator<char>());
    // Field 2, the command name, may contain spaces and parentheses, so the fixed fields are
    // counted from the last ')'.
    auto close = text.rfind(')');
    if (close == std::string::npos)
        return 0;
    std::istringstream rest(text.substr(close + 1));
    std::string field;
    // A zombie has exited but keeps its /proc entry until its parent reaps it.
    if (!(rest >> field) || field == "Z" || field == "X")
        return 0;
    // Field 3 was the state; starttime is field 22.
    for (int i = 4; i < 22 && rest >> field; ++i) {
    }
    std::uint64_t start = 0;
    rest >> start;
    return start;
}

namespace {

// getpid() is a real system call on Linux (neither glibc nor musl cache it), so the pid and
// start time are read once and read again in every child created by fork, whoever calls fork.
std::atomic<std::uint32_t> cached_pid{0};
std::atomic<std::uint64_t> cached_start{0};

void read_self() {
    auto pid = static_cast<std::uint32_t>(getpid());
    cached_pid.store(pid, std::memory_order_relaxed);
    cached_start.store(process_start(pid), std::memory_order_relaxed);
}

[[maybe_unused]] const bool self_tracked = (read_self(), pthread_atfork(nullptr, nullptr, read_self) == 0);

} // namespace

ProcessId current_process() {
    return {cached_pid.load(std::memory_order_relaxed), cached_start.load(std::memory_order_relaxed)};
}

#endif

bool process_alive(const ProcessId &id) { return id.pid != 0 && process_start(id.pid) == id.start; }

} // namespace sharedbox
