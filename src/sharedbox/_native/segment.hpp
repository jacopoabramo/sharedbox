#pragma once

#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace sharedbox {

enum class FieldKind : std::uint8_t { Fixed = 0, Prefixed = 1 };

struct FieldDesc {
    std::uint64_t offset;
    std::uint32_t capacity;
    FieldKind kind;
};

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

/// A named shared-memory segment holding one fixed-layout record.
class Segment {
public:
    static std::unique_ptr<Segment> create(const std::string &name, const std::vector<FieldDesc> &fields,
                                           std::uint64_t record_size, std::uint64_t schema_hash,
                                           double lock_timeout);
    static std::unique_ptr<Segment> attach(const std::string &name, std::uint64_t schema_hash,
                                           double lock_timeout);
    ~Segment();
    Segment(const Segment &) = delete;
    Segment &operator=(const Segment &) = delete;

    std::string read(std::uint32_t field) const;
    std::vector<std::string> read_all() const;
    void write(const std::vector<std::pair<std::uint32_t, std::string>> &values);
    std::uint64_t version(std::uint32_t field) const;
    std::uint64_t generation() const;
    /// Returns the generation once it differs from last_generation, or after timeout seconds.
    std::uint64_t wait(std::uint64_t last_generation, double timeout) const;
    void force_unlock();
    /// Takes the write lock and never releases it; exists for tests.
    void hold_write_lock();
    /// Detaches this handle; the segment itself stays until unlinked.
    void close();
    bool closed() const;
    const std::string &name() const;
    /// Removes the name, like shm_unlink: existing handles keep working. A no-op on
    /// Windows, where the OS frees the segment when its last handle closes.
    static void unlink(const std::string &name);

private:
    struct Impl;
    explicit Segment(std::unique_ptr<Impl> impl);
    std::unique_ptr<Impl> impl_;
};

} // namespace sharedbox
