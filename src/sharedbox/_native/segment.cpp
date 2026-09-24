#include "segment.hpp"

#include "notifier.hpp"

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <mutex>
#include <new>
#include <shared_mutex>
#include <system_error>
#include <thread>

#include <boost/interprocess/exceptions.hpp>
#include <boost/interprocess/permissions.hpp>
#ifdef _WIN32
#include <boost/interprocess/managed_windows_shared_memory.hpp>
#include <windows.h>
#else
#include <boost/interprocess/managed_shared_memory.hpp>
#include <boost/interprocess/shared_memory_object.hpp>
#include <unistd.h>
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
constexpr std::uint32_t kAbiVersion = 1;
constexpr std::uint32_t kMaxFields = 256;
constexpr std::uint32_t kMaxCapacity = 1u << 20;
constexpr const char *kHeaderName = "sharedbox.header";

static_assert(std::atomic<std::uint64_t>::is_always_lock_free);
static_assert(std::atomic<std::uint32_t>::is_always_lock_free);

struct StoredField {
    std::uint64_t offset;
    std::uint32_t capacity;
    std::uint8_t kind;
    std::uint8_t reserved[3];
};

struct Header {
    std::atomic<std::uint64_t> magic;
    std::uint32_t abi_version;
    std::uint32_t field_count;
    std::uint64_t schema_hash;
    std::uint64_t record_size;
    Managed::handle_t record;
    std::atomic<std::uint64_t> seq;
    std::atomic<std::uint64_t> generation;
    std::atomic<std::uint32_t> wake_word;
    std::atomic<std::uint32_t> waiters;
    std::atomic<std::int64_t> writer_pid;
    StoredField fields[kMaxFields];
    std::atomic<std::uint64_t> versions[kMaxFields];
};

using Clock = std::chrono::steady_clock;

Clock::duration to_duration(double seconds) {
    return std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds));
}

std::int64_t current_pid() {
#ifdef _WIN32
    return static_cast<std::int64_t>(GetCurrentProcessId());
#else
    return static_cast<std::int64_t>(getpid());
#endif
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
    explicit Backoff(double timeout) : deadline_(Clock::now() + to_duration(timeout)) {}
    bool expired() const { return Clock::now() >= deadline_; }
    void pause() {
        if (++spins_ < 64)
            cpu_relax();
        else
            std::this_thread::yield();
    }

private:
    Clock::time_point deadline_;
    unsigned spins_ = 0;
};

bool field_fits(std::uint64_t offset, std::uint32_t capacity, std::uint8_t kind, std::uint64_t record_size) {
    if (kind > 1 || capacity == 0 || capacity > kMaxCapacity || offset % 8 != 0 || offset > record_size)
        return false;
    std::uint64_t span = capacity + (kind == 1 ? sizeof(std::uint32_t) : 0);
    return span <= record_size - offset;
}

std::size_t segment_size(std::uint64_t record_size) {
    constexpr std::size_t kGranularity = 64 * 1024;
    std::size_t raw = sizeof(Header) + static_cast<std::size_t>(record_size) + kGranularity;
    return (raw + kGranularity - 1) / kGranularity * kGranularity;
}

} // namespace

struct Segment::Impl {
    std::string name;
    Managed segment;
    Header *header = nullptr;
    unsigned char *record = nullptr;
    // Validated copy of the header's field table; the shared one can be rewritten by any process.
    std::vector<StoredField> fields;
    double lock_timeout = 5.0;
    std::atomic<bool> closed{false};
    std::unique_ptr<Notifier> notifier;
    // Without a GIL (free-threaded builds) close() can race any other call; it takes this exclusively.
    mutable std::shared_mutex lifetime;

    void check_open() const {
        if (closed)
            throw SegmentClosed("box '" + name + "' is closed");
    }

    const StoredField &field(std::uint32_t index) const {
        if (index >= fields.size())
            throw std::out_of_range("field index " + std::to_string(index) + " is out of range");
        return fields[index];
    }

    // Races with writers by design; read_consistent discards copies taken during a write.
    void copy_out(const StoredField &f, std::string &out) const {
        const unsigned char *src = record + f.offset;
        if (f.kind == static_cast<std::uint8_t>(FieldKind::Fixed)) {
            out.assign(reinterpret_cast<const char *>(src), f.capacity);
            return;
        }
        std::uint32_t length;
        std::memcpy(&length, src, sizeof length);
        if (length > f.capacity)
            length = f.capacity;
        out.assign(reinterpret_cast<const char *>(src + sizeof length), length);
    }

    template <class Copy> void read_consistent(Copy &&copy) const {
        Backoff backoff(lock_timeout);
        for (;;) {
            std::uint64_t before = header->seq.load(std::memory_order_acquire);
            if ((before & 1u) == 0) {
                copy();
                std::atomic_thread_fence(std::memory_order_acquire);
                if (header->seq.load(std::memory_order_relaxed) == before)
                    return;
            }
            if (backoff.expired())
                throw_lock_timeout();
            backoff.pause();
        }
    }

    void lock() {
        Backoff backoff(lock_timeout);
        for (;;) {
            std::uint64_t seq = header->seq.load(std::memory_order_relaxed);
            if ((seq & 1u) == 0 && header->seq.compare_exchange_weak(seq, seq + 1, std::memory_order_acquire,
                                                                     std::memory_order_relaxed)) {
                std::atomic_thread_fence(std::memory_order_release);
                header->writer_pid.store(current_pid(), std::memory_order_relaxed);
                return;
            }
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
                                         std::uint64_t record_size, std::uint64_t schema_hash,
                                         double lock_timeout) {
    if (fields.empty() || fields.size() > kMaxFields)
        throw std::invalid_argument("a box needs between 1 and 256 fields");
    // Bounds record_size so segment_size()'s addition cannot wrap: without this, a
    // record_size near 2^64 makes segment_size() overflow to a small value, create_only
    // then succeeds against that small size, and allocate_aligned(record_size) throws
    // afterwards, leaving the name behind.
    constexpr std::uint64_t kMaxRecordSize = static_cast<std::uint64_t>(kMaxFields) * (kMaxCapacity + 8);
    if (record_size > kMaxRecordSize)
        throw std::invalid_argument("record_size " + std::to_string(record_size) + " exceeds the maximum of " +
                                    std::to_string(kMaxRecordSize));
    for (const auto &f : fields)
        if (!field_fits(f.offset, f.capacity, static_cast<std::uint8_t>(f.kind), record_size))
            throw std::invalid_argument("field at offset " + std::to_string(f.offset) + " does not fit the record");

    auto impl = std::make_unique<Impl>();
    impl->name = name;
    impl->lock_timeout = lock_timeout;

    bipc::permissions perms;
#ifndef _WIN32
    perms.set_permissions(0600);
#endif
    try {
        impl->segment = Managed(bipc::create_only, name.c_str(), segment_size(record_size), nullptr, perms);
    } catch (const bipc::interprocess_exception &e) {
        if (e.get_error_code() == bipc::already_exists_error)
            throw SegmentExists("a segment named '" + name + "' already exists");
        throw;
    }

    try {
        Header *h = impl->segment.construct<Header>(kHeaderName)();
        void *record = impl->segment.allocate_aligned(static_cast<std::size_t>(record_size), 64);
        std::memset(record, 0, static_cast<std::size_t>(record_size));
        h->abi_version = kAbiVersion;
        h->field_count = static_cast<std::uint32_t>(fields.size());
        h->schema_hash = schema_hash;
        h->record_size = record_size;
        h->record = impl->segment.get_handle_from_address(record);
        for (std::size_t i = 0; i < fields.size(); ++i) {
            h->fields[i] = StoredField{fields[i].offset, fields[i].capacity, static_cast<std::uint8_t>(fields[i].kind), {0, 0, 0}};
            impl->fields.push_back(h->fields[i]);
        }
        h->magic.store(kMagic, std::memory_order_release);

        impl->header = h;
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

std::unique_ptr<Segment> Segment::attach(const std::string &name, std::uint64_t schema_hash, double lock_timeout) {
    auto impl = std::make_unique<Impl>();
    impl->name = name;
    impl->lock_timeout = lock_timeout;
    try {
        impl->segment = Managed(bipc::open_only, name.c_str());
    } catch (const bipc::interprocess_exception &e) {
        if (e.get_error_code() == bipc::not_found_error)
            throw SegmentMissing("no segment named '" + name + "'");
        throw;
    }

    auto found = impl->segment.find<Header>(kHeaderName);
    Header *h = found.first;
    if (h == nullptr || found.second != 1)
        throw SchemaMismatch("segment '" + name + "' is not a sharedbox");
    Backoff wait_for_creator(1.0);
    while (h->magic.load(std::memory_order_acquire) != kMagic) {
        if (wait_for_creator.expired())
            throw SchemaMismatch("segment '" + name + "' is not a sharedbox");
        wait_for_creator.pause();
    }
    if (h->abi_version != kAbiVersion)
        throw SchemaMismatch("segment '" + name + "' uses layout version " + std::to_string(h->abi_version));
    if (h->schema_hash != schema_hash)
        throw SchemaMismatch("segment '" + name + "' was created by a different class");

    // get_size() reads Boost's own allocator header inside the segment, not the OS
    // mapping; find<>() above already trusts that same header, so this check catches
    // corruption but not a deliberately forged header, which only a process that can
    // already write the segment could produce.
    const std::uint64_t size = impl->segment.get_size();
    const std::uint64_t record_size = h->record_size;
    const std::uint32_t count = h->field_count;
    const Managed::handle_t record_handle = h->record;
    if (count == 0 || count > kMaxFields || record_handle < 0 || static_cast<std::uint64_t>(record_handle) >= size ||
        record_size > size - static_cast<std::uint64_t>(record_handle))
        throw SchemaMismatch("segment '" + name + "' has a corrupt header");
    for (std::uint32_t i = 0; i < count; ++i) {
        StoredField f = h->fields[i];
        if (!field_fits(f.offset, f.capacity, f.kind, record_size))
            throw SchemaMismatch("segment '" + name + "' has a corrupt header");
        impl->fields.push_back(f);
    }

    impl->header = h;
    impl->record = static_cast<unsigned char *>(impl->segment.get_address_from_handle(record_handle));
    impl->notifier = std::make_unique<Notifier>(name, impl->header->wake_word, impl->header->waiters);
    return std::unique_ptr<Segment>(new Segment(std::move(impl)));
}

std::string Segment::read(std::uint32_t index) const {
    std::shared_lock guard(impl_->lifetime);
    impl_->check_open();
    const StoredField &f = impl_->field(index);
    std::string out;
    impl_->read_consistent([&] { impl_->copy_out(f, out); });
    return out;
}

std::vector<std::string> Segment::read_all() const {
    std::shared_lock guard(impl_->lifetime);
    impl_->check_open();
    std::vector<std::string> out(impl_->fields.size());
    impl_->read_consistent([&] {
        for (std::size_t i = 0; i < out.size(); ++i)
            impl_->copy_out(impl_->fields[i], out[i]);
    });
    return out;
}

void Segment::write(const std::vector<std::pair<std::uint32_t, std::string>> &values) {
    std::shared_lock guard(impl_->lifetime);
    impl_->check_open();
    for (const auto &[index, bytes] : values) {
        const StoredField &f = impl_->field(index);
        bool fixed = f.kind == static_cast<std::uint8_t>(FieldKind::Fixed);
        if (fixed ? bytes.size() != f.capacity : bytes.size() > f.capacity)
            throw std::invalid_argument("value for field " + std::to_string(index) + " is " +
                                        std::to_string(bytes.size()) + " bytes; the field holds " +
                                        std::to_string(f.capacity));
    }
    impl_->lock();
    for (const auto &[index, bytes] : values) {
        const StoredField &f = impl_->fields[index];
        unsigned char *dst = impl_->record + f.offset;
        if (f.kind == static_cast<std::uint8_t>(FieldKind::Fixed)) {
            std::memcpy(dst, bytes.data(), bytes.size());
        } else {
            auto length = static_cast<std::uint32_t>(bytes.size());
            std::memcpy(dst, &length, sizeof length);
            std::memcpy(dst + sizeof length, bytes.data(), bytes.size());
        }
        impl_->header->versions[index].fetch_add(1, std::memory_order_relaxed);
    }
    impl_->unlock();
    impl_->header->generation.fetch_add(1, std::memory_order_seq_cst);
    impl_->notifier->wake_all();
}

std::uint64_t Segment::version(std::uint32_t index) const {
    std::shared_lock guard(impl_->lifetime);
    impl_->check_open();
    impl_->field(index);
    return impl_->header->versions[index].load(std::memory_order_acquire);
}

std::uint64_t Segment::generation() const {
    std::shared_lock guard(impl_->lifetime);
    impl_->check_open();
    return impl_->header->generation.load(std::memory_order_acquire);
}

std::uint64_t Segment::wait(std::uint64_t last_generation, double timeout) const {
    std::shared_lock guard(impl_->lifetime);
    impl_->check_open();
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
    std::shared_lock guard(impl_->lifetime);
    impl_->check_open();
    std::uint64_t seq = impl_->header->seq.load(std::memory_order_relaxed);
    if (seq & 1u)
        impl_->header->seq.compare_exchange_strong(seq, seq + 1, std::memory_order_release);
}

void Segment::hold_write_lock() {
    std::shared_lock guard(impl_->lifetime);
    impl_->check_open();
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

bool Segment::closed() const { return impl_->closed; }

const std::string &Segment::name() const { return impl_->name; }

} // namespace sharedbox
