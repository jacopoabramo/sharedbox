#include "liveness.hpp"

#ifdef _WIN32
#include <windows.h>
#else
#include <atomic>
#include <cerrno>
#include <csignal>
#include <fstream>
#include <iterator>
#include <limits>
#include <pthread.h>
#include <sstream>
#include <string>
#include <sys/types.h>
#include <unistd.h>
#endif

namespace sharedbox {

#ifdef _WIN32

std::uint64_t process_start(std::uint32_t pid) {
    HANDLE process = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (process == nullptr)
        return GetLastError() == ERROR_ACCESS_DENIED ? kStartUnknown : 0;
    std::uint64_t start = 0;
    // A process that has exited stays queryable while any handle to it is open, and its exit
    // code may be any value, STILL_ACTIVE included; only the handle's signal state is reliable.
    if (WaitForSingleObject(process, 0) == WAIT_TIMEOUT) {
        FILETIME created, exited, kernel, user;
        start = GetProcessTimes(process, &created, &exited, &kernel, &user)
                    ? (static_cast<std::uint64_t>(created.dwHighDateTime) << 32) | created.dwLowDateTime
                    : kStartUnknown;
    }
    CloseHandle(process);
    return start;
}

ProcessId current_process() {
    static const ProcessId self{GetCurrentProcessId(), process_start(GetCurrentProcessId())};
    return self;
}

#else

std::uint64_t process_start(std::uint32_t pid) {
    // kill(0, ...) and negative pids address process groups, not one process.
    if (pid == 0 || pid > static_cast<std::uint32_t>(std::numeric_limits<pid_t>::max()))
        return 0;
    bool denied = false;
    if (kill(static_cast<pid_t>(pid), 0) != 0) {
        if (errno == ESRCH)
            return 0;
        denied = errno == EPERM;
    }
    std::ifstream stat("/proc/" + std::to_string(pid) + "/stat");
    // /proc mounted with hidepid hides processes of other users, which kill reports as EPERM.
    if (!stat)
        return denied ? kStartUnknown : 0;
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

// getpid() is a real system call on Linux (neither glibc nor musl cache it), so the pid is read
// once and read again in every child created by fork, whoever calls fork. The start time is
// read on first use rather than in the fork handler, which should stay short.
std::atomic<std::uint32_t> cached_pid{0};
std::atomic<std::uint64_t> cached_start{0};

void read_pid() {
    cached_pid.store(static_cast<std::uint32_t>(getpid()), std::memory_order_relaxed);
    cached_start.store(0, std::memory_order_relaxed);
}

[[maybe_unused]] const bool pid_tracked = (read_pid(), pthread_atfork(nullptr, nullptr, read_pid) == 0);

} // namespace

ProcessId current_process() {
    auto pid = cached_pid.load(std::memory_order_relaxed);
    auto start = cached_start.load(std::memory_order_relaxed);
    // Threads racing here all read the same value, so the last store wins harmlessly.
    if (start == 0) {
        start = process_start(pid);
        cached_start.store(start, std::memory_order_relaxed);
    }
    return {pid, start};
}

#endif

bool process_alive(const ProcessId &id) {
    if (id.pid == 0)
        return false;
    auto start = process_start(id.pid);
    return start != 0 && (start == kStartUnknown || id.start == kStartUnknown || start == id.start);
}

} // namespace sharedbox
