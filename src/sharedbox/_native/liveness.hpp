#pragma once

#include <cstdint>

namespace sharedbox {

/// What process_start returns for a process that exists but cannot be inspected.
inline constexpr std::uint64_t kStartUnknown = UINT64_MAX;

/// A process, told apart from a later one that reuses its pid by its start time.
struct ProcessId {
    std::uint32_t pid;
    std::uint64_t start;
};

/// When the process started, in an OS-specific unit. 0 means no running process has that pid;
/// kStartUnknown means one does, but its start time cannot be read.
std::uint64_t process_start(std::uint32_t pid);

/// This process.
ProcessId current_process();

/// False when no process has that pid, or one does with a different start time. A process whose
/// start time cannot be read counts as alive.
bool process_alive(const ProcessId &id);

} // namespace sharedbox
