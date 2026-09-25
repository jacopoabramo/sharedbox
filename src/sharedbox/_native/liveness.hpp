#pragma once

#include <cstdint>

namespace sharedbox {

/// A process, told apart from a later one that reuses its pid by its start time.
struct ProcessId {
    std::uint32_t pid;
    std::uint64_t start;
};

/// When the process started, in an OS-specific unit; 0 if no running process has that pid.
std::uint64_t process_start(std::uint32_t pid);

/// This process.
ProcessId current_process();

/// False when no process has that pid, or one does with a different start time.
bool process_alive(const ProcessId &id);

} // namespace sharedbox
