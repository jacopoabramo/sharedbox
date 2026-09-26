#include "segment.hpp"

#include "liveness.hpp"
#include "notifier.hpp"

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstring>
#include <memory>
#include <mutex>
#include <new>
#include <optional>
#include <shared_mutex>
#include <system_error>
#include <thread>
#include <type_traits>

#include <boost/interprocess/exceptions.hpp>
#include <boost/interprocess/permissions.hpp>
#ifdef _WIN32
#include <boost/interprocess/managed_windows_shared_memory.hpp>
#include <windows.h>
#else
#include <boost/interprocess/managed_shared_memory.hpp>
#include <boost/interprocess/shared_memory_object.hpp>
#endif

namespace bipc = boost::interprocess;

namespace sharedbox {
namespace {

#ifdef _WIN32
using Managed = bipc::managed_windows_shared_memory;
#else
using Managed = bipc::managed_shared_memory;
#endif

constexpr std::uint64_t kMagic = 0x3158424445524853ull;
constexpr std::uint32_t kAbiVersion = 3;
constexpr std::uint32_t kMaxFields = 256;
constexpr std::uint32_t kMaxCapacity = 1u << 20;
constexpr std::uint64_t kMaxRecordSize = static_cast<std::uint64_t>(kMaxFields) * (kMaxCapacity + 8);
// A capacity needs 21 bits and a kind 3, so both share one word and the entry stays 8 bytes.
constexpr std::uint32_t kKindShift = 24;
constexpr std::uint32_t kCapacityMask = (1u << kKindShift) - 1;
constexpr std::size_t kRecordAlignment = 64;
// Boost 1.92 needs at most 552 bytes for its own bookkeeping and the named header's entry,
// measured on Windows and on Linux (glibc) by shrinking the segment until creation failed.
constexpr std::size_t kBoostOverhead = 1024;
// Rounding to a smaller unit than the real page size only makes the OS round up again.
constexpr std::size_t kPageSize = 4096;
constexpr const char *kHeaderName = "sharedbox.header";

using Version = std::atomic<std::uint64_t>;

static_assert(std::atomic<std::uint64_t>::is_always_lock_free);
static_assert(std::atomic<std::uint32_t>::is_always_lock_free);
// Earlier segment managers ignore alignas when constructing a named object.
static_assert(BOOST_INTERPROCESS_SEGMENT_MANAGER_ABI >= 2);

// capacity_and_kind holds the capacity in its low 24 bits and the kind code in the top 8.
struct StoredField {
    std::uint32_t offset;
    std::uint32_t capacity_and_kind;
};
static_assert(std::is_standard_layout_v<StoredField>);
static_assert(sizeof(StoredField) == 8 && alignof(StoredField) == 4);
static_assert(offsetof(StoredField, offset) == 0 && offsetof(StoredField, capacity_and_kind) == 4);

// One cache line. Everything before writer_pid is written at creation and read only by
// attach, so sharing the line with the members every write changes costs nothing, and a
// write touches a single header line. magic and abi_version keep their offsets in every
// layout version, so attach can name the version of an older segment.
struct alignas(64) Header {
    std::atomic<std::uint64_t> magic;
    std::uint32_t abi_version;
    std::uint32_t field_count;
    std::uint64_t schema_hash;
    std::uint32_t record_size;
    std::uint32_t record;
    std::uint32_t tail;
    std::atomic<std::uint32_t> writer_pid;
    std::atomic<std::uint64_t> seq;
    std::atomic<std::uint64_t> generation;
    std::atomic<std::uint32_t> wake_word;
    std::atomic<std::uint32_t> waiters;
};
static_assert(std::is_standard_layout_v<Header>);
static_assert(sizeof(Header) == 64 && alignof(Header) == 64);
static_assert(offsetof(Header, magic) == 0 && offsetof(Header, abi_version) == 8);
static_assert(offsetof(Header, field_count) == 12 && offsetof(Header, schema_hash) == 16);
static_assert(offsetof(Header, record_size) == 24 && offsetof(Header, record) == 28);
static_assert(offsetof(Header, tail) == 32 && offsetof(Header, writer_pid) == 36);
static_assert(offsetof(Header, seq) == 40 && offsetof(Header, generation) == 48);
static_assert(offsetof(Header, wake_word) == 56 && offsetof(Header, waiters) == 60);
static_assert(sizeof(Version) == 8 && alignof(Version) == 8);

// The tail holds field_count StoredField entries followed by field_count versions; entries
// are 8 bytes, so the versions stay 8-byte aligned when the tail is.
constexpr std::size_t tail_size(std::size_t field_count) {
    return field_count * (sizeof(StoredField) + sizeof(Version));
}

constexpr std::size_t segment_size(std::uint64_t record_size, std::size_t field_count) {
    std::size_t raw = sizeof(Header) + tail_size(field_count) + kRecordAlignment - 1 +
                      static_cast<std::size_t>(record_size) + kBoostOverhead;
    return (raw + kPageSize - 1) / kPageSize * kPageSize;
}
// Offsets into the segment are stored in 32 bits.
static_assert(segment_size(kMaxRecordSize, kMaxFields) <= UINT32_MAX);

using Clock = std::chrono::steady_clock;

Clock::duration to_duration(double seconds) {
    return std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds));
}

void cpu_relax() {
#if defined(_WIN32)
    YieldProcessor();
#elif defined(__x86_64__) || defined(__i386__)
    __builtin_ia32_pause();
#elif defined(__aarch64__)
    asm volatile("yield");
#endif
}

class Backoff {
public:
    explicit Backoff(double timeout) : timeout_(timeout) {}

    // Read the clock only after the first failed attempt, so an uncontended lock or read
    // makes no clock call; the timeout counts from that attempt.
    bool expired() {
        Clock::time_point now = Clock::now();
        if (!deadline_) {
            deadline_ = now + to_duration(timeout_);
            return false;
        }
        return now >= *deadline_;
    }

    void pause() {
        if (++spins_ < 64)
            cpu_relax();
        else
            std::this_thread::yield();
    }

private:
    double timeout_;
    std::optional<Clock::time_point> deadline_;
    unsigned spins_ = 0;
};

void check_lock_timeout(double lock_timeout) {
    if (!(std::isfinite(lock_timeout) && lock_timeout > 0 && lock_timeout <= 86400))
        throw std::invalid_argument("lock_timeout must be finite and in (0, 86400]");
}

bool field_fits(const FieldDesc &f, std::uint64_t record_size) {
    if (!kind_is_valid(static_cast<std::uint32_t>(f.kind)) || f.capacity == 0 || f.capacity > kMaxCapacity ||
        f.offset % field_alignment(f.kind) != 0 || f.offset > record_size)
        return false;
    if (!is_prefixed(f.kind) && f.capacity != (f.kind == FieldKind::Bool ? 1u : 8u))
        return false;
    return field_span(f) <= record_size - f.offset;
}

void check_values(const std::vector<FieldDesc> &fields,
                  const std::vector<std::pair<std::uint32_t, std::string>> &values) {
    for (const auto &[index, bytes] : values) {
        if (index >= fields.size())
            throw std::out_of_range("field index " + std::to_string(index) + " is out of range");
        const FieldDesc &f = fields[index];
        if (is_prefixed(f.kind) ? bytes.size() > f.capacity : bytes.size() != f.capacity)
            throw std::invalid_argument("value for field " + std::to_string(index) + " is " +
                                        std::to_string(bytes.size()) + " bytes; the field holds " +
                                        std::to_string(f.capacity));
    }
}

void store(unsigned char *record, const FieldDesc &f, const std::string &bytes) {
    unsigned char *dst = record + f.offset;
    if (is_prefixed(f.kind)) {
        auto length = static_cast<std::uint32_t>(bytes.size());
        std::memcpy(dst, &length, sizeof length);
        dst += sizeof length;
    }
    std::memcpy(dst, bytes.data(), bytes.size());
}

void check_names(const std::vector<std::string> &names, std::size_t count) {
    if (names.size() != count)
        throw std::invalid_argument("got " + std::to_string(names.size()) + " field names for " +
                                    std::to_string(count) + " fields");
}

WaitHook before_wait = []() -> void * { return nullptr; };
ResumeHook after_wait = [](void *) {};

class WaitScope {
public:
    WaitScope() : state_(before_wait()) {}
    ~WaitScope() { after_wait(state_); }
    WaitScope(const WaitScope &) = delete;
    WaitScope &operator=(const WaitScope &) = delete;

private:
    void *state_;
};

} // namespace

bool kind_is_valid(std::uint32_t code) { return code <= static_cast<std::uint32_t>(FieldKind::Bytes); }

bool is_prefixed(FieldKind kind) { return kind == FieldKind::Str || kind == FieldKind::Bytes; }

std::size_t field_alignment(FieldKind kind) {
    switch (kind) {
    case FieldKind::Bool:
        return 1;
    case FieldKind::Int:
    case FieldKind::Float:
        return 8;
    default:
        return 4;
    }
}

std::size_t field_span(const FieldDesc &field) {
    return is_prefixed(field.kind) ? sizeof(std::uint32_t) + field.capacity : field.capacity;
}

void set_wait_hooks(WaitHook before, ResumeHook after) {
    before_wait = before;
    after_wait = after;
}

struct Segment::Impl {
    std::string name;
    Managed segment;
    Header *header = nullptr;
    unsigned char *record = nullptr;
    Version *versions = nullptr;
    // Validated copy of the field table; the shared one can be rewritten by any process.
    std::vector<FieldDesc> fields;
    std::vector<std::string> names;
    double lock_timeout = 5.0;
    std::atomic<bool> closed{false};
    std::unique_ptr<Notifier> notifier;
    // close() can race any other call on every build, since lock waits release the GIL and the
    // free-threaded build has none; it takes this exclusively.
    mutable std::shared_mutex lifetime;

    void check_open() const {
        if (closed)
            throw SegmentClosed("box '" + name + "' is closed");
    }

    // Never blocks while close() holds or waits for the lock: close() may be waiting for a
    // thread that released the GIL inside a lock wait and needs it back before it can leave,
    // so a caller holding the GIL that blocked here would never be let through.
    std::shared_lock<std::shared_mutex> enter() const {
        std::shared_lock guard(lifetime, std::try_to_lock);
        while (!guard.owns_lock()) {
            check_open();
            std::this_thread::yield();
            guard.try_lock();
        }
        check_open();
        return guard;
    }

    const FieldDesc &field(std::uint32_t index) const {
        if (index >= fields.size())
            throw std::out_of_range("field index " + std::to_string(index) + " is out of range");
        return fields[index];
    }

    // Races with writers by design; read_consistent discards copies taken during a write.
    void copy_out(const FieldDesc &f, std::string &out) const {
        const unsigned char *src = record + f.offset;
        if (!is_prefixed(f.kind)) {
            out.assign(reinterpret_cast<const char *>(src), f.capacity);
            return;
        }
        std::uint32_t length;
        std::memcpy(&length, src, sizeof length);
        if (length > f.capacity)
            length = f.capacity;
        out.assign(reinterpret_cast<const char *>(src + sizeof length), length);
    }

    template <class Copy> bool try_copy(Copy &copy) const {
        std::uint64_t before = header->seq.load(std::memory_order_acquire);
        if (before & 1u)
            return false;
        copy();
        std::atomic_thread_fence(std::memory_order_acquire);
        return header->seq.load(std::memory_order_relaxed) == before;
    }

    template <class Copy> void read_consistent(Copy &&copy) const {
        if (try_copy(copy)) [[likely]]
            return;
        WaitScope waiting;
        Backoff backoff(lock_timeout);
        while (!try_copy(copy)) {
            if (backoff.expired())
                throw_lock_timeout();
            backoff.pause();
        }
    }

    bool try_lock() {
        std::uint64_t seq = header->seq.load(std::memory_order_relaxed);
        if ((seq & 1u) != 0 ||
            !header->seq.compare_exchange_weak(seq, seq + 1, std::memory_order_acquire, std::memory_order_relaxed))
            return false;
        std::atomic_thread_fence(std::memory_order_release);
        header->writer_pid.store(current_pid(), std::memory_order_relaxed);
        return true;
    }

    void lock() {
        if (try_lock()) [[likely]]
            return;
        WaitScope waiting;
        Backoff backoff(lock_timeout);
        while (!try_lock()) {
            if (backoff.expired())
                throw_lock_timeout();
            backoff.pause();
        }
    }

    void unlock() {
        header->writer_pid.store(0, std::memory_order_relaxed);
        header->seq.fetch_add(1, std::memory_order_release);
    }

    [[noreturn]] void throw_lock_timeout() const {
        throw LockTimeout("box '" + name + "' is locked by pid " +
                          std::to_string(header->writer_pid.load(std::memory_order_relaxed)) +
                          "; call force_unlock() if that process is gone");
    }
};

Segment::Segment(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}

Segment::~Segment() {
    try {
        close();
    } catch (...) {
    }
}

std::unique_ptr<Segment> Segment::create(const std::string &name, const std::vector<FieldDesc> &fields,
                                         const std::vector<std::string> &names, std::uint64_t record_size,
                                         std::uint64_t schema_hash, double lock_timeout,
                                         const std::vector<std::pair<std::uint32_t, std::string>> &values) {
    check_lock_timeout(lock_timeout);
    if (fields.empty() || fields.size() > kMaxFields)
        throw std::invalid_argument("a box needs between 1 and 256 fields");
    check_names(names, fields.size());
    // Bounds record_size so segment_size()'s addition cannot wrap: without this, a
    // record_size near 2^64 makes segment_size() overflow to a small value, create_only
    // then succeeds against that small size, and allocating the record throws afterwards,
    // leaving the name behind.
    if (record_size > kMaxRecordSize)
        throw std::invalid_argument("record_size " + std::to_string(record_size) + " exceeds the maximum of " +
                                    std::to_string(kMaxRecordSize));
    for (const auto &f : fields)
        if (!field_fits(f, record_size))
            throw std::invalid_argument("field at offset " + std::to_string(f.offset) +
                                        " does not fit the record");
    check_values(fields, values);

    auto impl = std::make_unique<Impl>();
    impl->name = name;
    impl->names = names;
    impl->lock_timeout = lock_timeout;

    bipc::permissions perms;
#ifndef _WIN32
    perms.set_permissions(0600);
#endif
    try {
        impl->segment =
            Managed(bipc::create_only, name.c_str(), segment_size(record_size, fields.size()), nullptr, perms);
    } catch (const bipc::interprocess_exception &e) {
        if (e.get_error_code() == bipc::already_exists_error)
            throw SegmentExists("a segment named '" + name + "' already exists");
        throw;
    }

    try {
        Header *h = impl->segment.construct<Header>(kHeaderName)();
        const std::size_t count = fields.size();
        const auto record_bytes = static_cast<std::size_t>(record_size);
        // Boost's allocate_aligned reserves twice the requested size while it looks for an
        // aligned address, so the tail and the record share one block, aligned here.
        std::size_t space = kRecordAlignment - 1 + record_bytes;
        auto *tail = static_cast<unsigned char *>(impl->segment.allocate(tail_size(count) + space));
        void *record = tail + tail_size(count);
        std::align(kRecordAlignment, record_bytes, record, space);
        std::memset(record, 0, record_bytes);
        auto *versions = reinterpret_cast<Version *>(tail + count * sizeof(StoredField));
        for (std::size_t i = 0; i < count; ++i) {
            const StoredField stored{
                fields[i].offset, fields[i].capacity | static_cast<std::uint32_t>(fields[i].kind) << kKindShift};
            std::memcpy(tail + i * sizeof(StoredField), &stored, sizeof stored);
            new (&versions[i]) Version(0);
        }
        impl->fields = fields;
        // Before magic is published, so an attacher never sees the record without these values.
        for (const auto &[index, bytes] : values)
            store(static_cast<unsigned char *>(record), fields[index], bytes);
        h->abi_version = kAbiVersion;
        h->field_count = static_cast<std::uint32_t>(count);
        h->schema_hash = schema_hash;
        h->record_size = static_cast<std::uint32_t>(record_size);
        h->record = static_cast<std::uint32_t>(impl->segment.get_handle_from_address(record));
        h->tail = static_cast<std::uint32_t>(impl->segment.get_handle_from_address(tail));
        h->magic.store(kMagic, std::memory_order_release);

        impl->header = h;
        impl->versions = versions;
        impl->record = static_cast<unsigned char *>(record);
        impl->notifier = std::make_unique<Notifier>(name, impl->header->wake_word, impl->header->waiters);
    } catch (...) {
        // The segment exists on disk/in /dev/shm from create_only above; without this
        // it would survive as a name no one can finish creating or safely attach to.
#ifndef _WIN32
        bipc::shared_memory_object::remove(name.c_str());
#endif
        throw;
    }
    return std::unique_ptr<Segment>(new Segment(std::move(impl)));
}

std::unique_ptr<Segment> Segment::attach(const std::string &name, const std::vector<std::string> &names,
                                         std::uint64_t schema_hash, double lock_timeout) {
    check_lock_timeout(lock_timeout);
    auto impl = std::make_unique<Impl>();
    impl->name = name;
    impl->names = names;
    impl->lock_timeout = lock_timeout;
    try {
        impl->segment = Managed(bipc::open_only, name.c_str());
    } catch (const bipc::interprocess_exception &e) {
        if (e.get_error_code() == bipc::not_found_error)
            throw SegmentMissing("no segment named '" + name + "'");
        throw;
    }

    // Found as bytes because an older layout's header has another size; magic and
    // abi_version are read at their fixed offsets before the size is compared. The lookup
    // is inside the wait because the creator may not have constructed the header yet.
    Backoff wait_for_creator(1.0);
    unsigned char *raw = nullptr;
    std::size_t raw_size = 0;
    for (;;) {
        auto found = impl->segment.find<unsigned char>(kHeaderName);
        raw = found.first;
        raw_size = found.second;
        if (raw != nullptr && raw_size >= offsetof(Header, abi_version) + sizeof(std::uint32_t) &&
            reinterpret_cast<std::uintptr_t>(raw) % alignof(std::uint64_t) == 0 &&
            reinterpret_cast<const std::atomic<std::uint64_t> *>(raw)->load(std::memory_order_acquire) == kMagic)
            break;
        if (wait_for_creator.expired())
            throw SchemaMismatch("segment '" + name + "' is not a sharedbox");
        wait_for_creator.pause();
    }
    std::uint32_t abi_version;
    std::memcpy(&abi_version, raw + offsetof(Header, abi_version), sizeof abi_version);
    if (abi_version != kAbiVersion)
        throw SchemaMismatch("segment '" + name + "' uses layout version " + std::to_string(abi_version));
    if (raw_size != sizeof(Header) || reinterpret_cast<std::uintptr_t>(raw) % alignof(Header) != 0)
        throw SchemaMismatch("segment '" + name + "' has a corrupt header");
    Header *h = reinterpret_cast<Header *>(raw);
    if (h->schema_hash != schema_hash)
        throw SchemaMismatch("segment '" + name + "' was created by a different class");

    // get_size() reads Boost's own allocator header inside the segment, not the OS
    // mapping; find<>() above already trusts that same header, so this check catches
    // corruption but not a deliberately forged header, which only a process that can
    // already write the segment could produce.
    const std::uint64_t size = impl->segment.get_size();
    const std::uint64_t record_size = h->record_size;
    const std::uint32_t count = h->field_count;
    const std::uint64_t record_handle = h->record;
    const std::uint64_t tail_handle = h->tail;
    if (count == 0 || count > kMaxFields || record_handle >= size || record_size > size - record_handle ||
        tail_handle >= size || tail_size(count) > size - tail_handle)
        throw SchemaMismatch("segment '" + name + "' has a corrupt header");
    check_names(names, count);
    auto *tail = static_cast<unsigned char *>(
        impl->segment.get_address_from_handle(static_cast<Managed::handle_t>(tail_handle)));
    if (reinterpret_cast<std::uintptr_t>(tail) % alignof(Version) != 0)
        throw SchemaMismatch("segment '" + name + "' has a corrupt header");
    for (std::uint32_t i = 0; i < count; ++i) {
        StoredField stored;
        std::memcpy(&stored, tail + i * sizeof(StoredField), sizeof stored);
        const std::uint32_t kind = stored.capacity_and_kind >> kKindShift;
        const FieldDesc f{stored.offset, stored.capacity_and_kind & kCapacityMask, static_cast<FieldKind>(kind)};
        if (!field_fits(f, record_size))
            throw SchemaMismatch("segment '" + name + "' has a corrupt header");
        impl->fields.push_back(f);
    }

    impl->header = h;
    impl->versions = reinterpret_cast<Version *>(tail + count * sizeof(StoredField));
    impl->record = static_cast<unsigned char *>(
        impl->segment.get_address_from_handle(static_cast<Managed::handle_t>(record_handle)));
    impl->notifier = std::make_unique<Notifier>(name, impl->header->wake_word, impl->header->waiters);
    return std::unique_ptr<Segment>(new Segment(std::move(impl)));
}

std::string Segment::read(std::uint32_t index) const {
    auto guard = impl_->enter();
    const FieldDesc &f = impl_->field(index);
    std::string out;
    impl_->read_consistent([&] { impl_->copy_out(f, out); });
    return out;
}

std::pair<std::uint64_t, std::string> Segment::read_versioned(std::uint32_t index) const {
    auto guard = impl_->enter();
    const FieldDesc &f = impl_->field(index);
    std::pair<std::uint64_t, std::string> out;
    impl_->read_consistent([&] {
        // Relaxed is enough: the sequence check discards a pair taken while a write ran.
        out.first = impl_->versions[index].load(std::memory_order_relaxed);
        impl_->copy_out(f, out.second);
    });
    return out;
}

std::vector<std::string> Segment::read_all() const {
    auto guard = impl_->enter();
    std::vector<std::string> out(impl_->fields.size());
    impl_->read_consistent([&] {
        for (std::size_t i = 0; i < out.size(); ++i)
            impl_->copy_out(impl_->fields[i], out[i]);
    });
    return out;
}

void Segment::write(const std::vector<std::pair<std::uint32_t, std::string>> &values) {
    auto guard = impl_->enter();
    check_values(impl_->fields, values);
    impl_->lock();
    for (const auto &[index, bytes] : values) {
        store(impl_->record, impl_->fields[index], bytes);
        impl_->versions[index].fetch_add(1, std::memory_order_relaxed);
    }
    impl_->unlock();
    impl_->header->generation.fetch_add(1, std::memory_order_seq_cst);
    impl_->notifier->wake_all();
}

std::uint64_t Segment::version(std::uint32_t index) const {
    auto guard = impl_->enter();
    impl_->field(index);
    return impl_->versions[index].load(std::memory_order_acquire);
}

std::uint64_t Segment::generation() const {
    auto guard = impl_->enter();
    return impl_->header->generation.load(std::memory_order_acquire);
}

std::uint64_t Segment::wait(std::uint64_t last_generation, double timeout) const {
    if (!(std::isfinite(timeout) && timeout >= 0 && timeout <= 86400))
        throw std::invalid_argument("timeout must be finite and in [0, 86400]");
    auto guard = impl_->enter();
    const Clock::time_point deadline = Clock::now() + to_duration(timeout);
    for (;;) {
        // Read the word before the generation: a write landing in between changes the word, so the wait returns.
        std::uint32_t word = impl_->header->wake_word.load(std::memory_order_seq_cst);
        std::uint64_t current = impl_->header->generation.load(std::memory_order_seq_cst);
        if (current != last_generation)
            return current;
        double remaining = std::chrono::duration<double>(deadline - Clock::now()).count();
        if (remaining <= 0)
            return current;
        impl_->notifier->wait(word, remaining);
    }
}

void Segment::force_unlock() {
    auto guard = impl_->enter();
    std::uint64_t seq = impl_->header->seq.load(std::memory_order_relaxed);
    if (seq & 1u)
        impl_->header->seq.compare_exchange_strong(seq, seq + 1, std::memory_order_release);
}

void Segment::hold_write_lock() {
    auto guard = impl_->enter();
    impl_->lock();
}

void Segment::after_fork() {
    // A thread of the parent may have held the lock at fork time; that thread does not exist in
    // the child, so the lock would never be released. The old one is overwritten, not destroyed,
    // because destroying a held mutex is undefined.
    new (&impl_->lifetime) std::shared_mutex();
}

void Segment::close() {
    // Flip the flag before taking the lock: libstdc++'s shared_mutex lets new readers
    // overtake a waiting writer, so a steady stream of readers could starve this lock
    // forever otherwise. check_open() now rejects every caller that arrives after this.
    if (impl_->closed.exchange(true))
        return;
    std::unique_lock guard(impl_->lifetime);
    impl_->notifier.reset();
    impl_->header = nullptr;
    impl_->versions = nullptr;
    impl_->record = nullptr;
    impl_->segment = Managed();
}

void Segment::unlink(const std::string &name) {
#ifndef _WIN32
    errno = 0;
    if (bipc::shared_memory_object::remove(name.c_str()))
        return;
    int error = errno;
    if (error == ENOENT)
        throw SegmentMissing("no segment named '" + name + "'");
    throw std::system_error(error, std::generic_category(), "cannot unlink segment '" + name + "'");
#else
    (void)name;
#endif
}

std::uint64_t Segment::size() const {
    auto guard = impl_->enter();
    return impl_->segment.get_size();
}

bool Segment::closed() const { return impl_->closed; }

const std::string &Segment::name() const { return impl_->name; }

double Segment::lock_timeout() const { return impl_->lock_timeout; }

const FieldDesc &Segment::field(std::uint32_t index) const { return impl_->field(index); }

const std::string &Segment::field_name(std::uint32_t index) const {
    impl_->field(index);
    return impl_->names[index];
}

} // namespace sharedbox
