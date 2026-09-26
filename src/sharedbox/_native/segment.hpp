#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace sharedbox {

enum class FieldKind : std::uint8_t { Bool = 0, Int = 1, Float = 2, Str = 3, Bytes = 4 };

struct FieldDesc {
    std::uint32_t offset;
    std::uint32_t capacity;
    FieldKind kind;
};

bool kind_is_valid(std::uint32_t code);
/// Throws std::out_of_range unless index < count.
void check_index(std::uint32_t index, std::size_t count);
/// Throws std::invalid_argument unless there is one name per field.
void check_names(const std::vector<std::string> &names, std::size_t count);
/// True for str and bytes, whose payload follows a 4-byte length.
bool is_prefixed(FieldKind kind);
std::size_t field_alignment(FieldKind kind);
/// Bytes the field occupies in the record, the length prefix included.
std::size_t field_span(const FieldDesc &field);

struct SegmentExists : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct SegmentMissing : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct SchemaMismatch : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct SegmentClosed : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct LockTimeout : std::runtime_error {
    using std::runtime_error::runtime_error;
};

using WaitHook = void *(*)();
using ResumeHook = void (*)(void *);
/// Called around a wait for another writer's lock; module.cpp releases the GIL there.
void set_wait_hooks(WaitHook before, ResumeHook after);

/// A named shared-memory segment holding one fixed-layout record.
class Segment {
public:
    /// values are written before any other process can attach; names label fields in error messages.
    static std::unique_ptr<Segment> create(const std::string &name, const std::vector<FieldDesc> &fields,
                                           const std::vector<std::string> &names, std::uint64_t record_size,
                                           std::uint64_t schema_hash, double lock_timeout,
                                           const std::vector<std::pair<std::uint32_t, std::string>> &values);
    static std::unique_ptr<Segment> attach(const std::string &name, const std::vector<std::string> &names,
                                           std::uint64_t schema_hash, double lock_timeout);
    ~Segment();
    Segment(const Segment &) = delete;
    Segment &operator=(const Segment &) = delete;

    std::string read(std::uint32_t field) const;
    /// The field's version and bytes, read together.
    std::pair<std::uint64_t, std::string> read_versioned(std::uint32_t field) const;
    std::vector<std::string> read_all() const;
    void write(const std::vector<std::pair<std::uint32_t, std::string>> &values);
    std::uint64_t version(std::uint32_t field) const;
    std::uint64_t generation() const;
    /// Returns the generation once it differs from last_generation, or after timeout seconds.
    std::uint64_t wait(std::uint64_t last_generation, double timeout) const;
    void force_unlock();
    /// Takes the write lock and never releases it; exists for tests.
    void hold_write_lock();
    /// Resets per-process locks in a child created by fork(); call before any other thread starts.
    void after_fork();
    /// Detaches this handle; the segment itself stays until unlinked.
    void close();
    /// Bytes of shared memory the segment manages.
    std::uint64_t size() const;
    bool closed() const;
    const std::string &name() const;
    double lock_timeout() const;
    const FieldDesc &field(std::uint32_t index) const;
    const std::string &field_name(std::uint32_t index) const;
    /// Removes the name, like shm_unlink: existing handles keep working. A no-op on
    /// Windows, where the OS frees the segment when its last handle closes.
    static void unlink(const std::string &name);

private:
    struct Impl;
    explicit Segment(std::unique_ptr<Impl> impl);
    std::unique_ptr<Impl> impl_;
};

} // namespace sharedbox
