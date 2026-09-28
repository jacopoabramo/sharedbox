#include "segment.hpp"

#include "notifier.hpp"

#include <sharedbox/sharedbox.hpp>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <iterator>
#include <limits>
#include <memory>
#include <mutex>
#include <new>
#include <shared_mutex>
#include <span>
#include <system_error>
#include <thread>

namespace sharedbox {
namespace {

using Clock = std::chrono::steady_clock;

void check_lock_timeout(double lock_timeout) {
    if (!(std::isfinite(lock_timeout) && lock_timeout > 0 && lock_timeout <= max_timeout))
        throw std::invalid_argument("lock_timeout must be finite and in (0, 86400]");
}

void check_values(const std::vector<FieldDesc> &fields, std::span<const value> values) {
    for (const value &v : values) {
        check_index(v.field, fields.size());
        const FieldDesc &f = fields[v.field];
        if (is_prefixed(f.kind) ? v.bytes.size() > f.capacity : v.bytes.size() != f.capacity)
            throw std::invalid_argument("value for field " + std::to_string(v.field) + " is " +
                                        std::to_string(v.bytes.size()) + " bytes; the field holds " +
                                        std::to_string(f.capacity));
    }
}

field_spec to_spec(const FieldDesc &f) { return {f.offset, f.capacity, static_cast<std::uint8_t>(f.kind)}; }

std::span<const std::byte> bytes_of(std::string_view s) { return std::as_bytes(std::span(s.data(), s.size())); }

WaitHook before_wait = []() -> void * { return nullptr; };
ResumeHook after_wait = [](void *) {};

// Reads the OS error first, before building the message can change it.
[[noreturn]] void throw_status(status code, const std::string &name) {
#ifdef _WIN32
    const int os_error = static_cast<int>(GetLastError());
    const std::error_category &category = std::system_category();
#else
    const int os_error = errno;
    const std::error_category &category = std::generic_category();
#endif
    switch (code) {
    case status::exists:
        throw SegmentExists("a segment named '" + name + "' already exists");
    case status::not_found:
        throw SegmentMissing("no segment named '" + name + "'");
    case status::corrupt:
        throw SchemaMismatch("segment '" + name + "' has a corrupt header");
    case status::range:
        throw std::invalid_argument("box '" + name + "': a field, size or name is out of range");
    case status::os:
        throw std::system_error(os_error, category, "box '" + name + "'");
    default:
        throw std::runtime_error("box '" + name + "': sharedbox.hpp returned " +
                                 std::to_string(static_cast<int>(code)));
    }
}

} // namespace

bool kind_is_valid(std::uint32_t code) { return code <= kind_bytes; }

void check_index(std::uint32_t index, std::size_t count) {
    if (index >= count)
        throw std::out_of_range("field index " + std::to_string(index) + " is out of range");
}

void check_names(const std::vector<std::string> &names, std::size_t count) {
    if (names.size() != count)
        throw std::invalid_argument("got " + std::to_string(names.size()) + " field names for " +
                                    std::to_string(count) + " fields");
}

bool is_prefixed(FieldKind kind) { return detail::prefixed(static_cast<std::uint32_t>(kind)); }

std::size_t field_alignment(FieldKind kind) { return detail::kind_alignment(static_cast<std::uint32_t>(kind)); }

std::size_t field_span(const FieldDesc &field) {
    return static_cast<std::size_t>(detail::field_span(to_spec(field)));
}

void set_wait_hooks(WaitHook before, ResumeHook after) {
    before_wait = before;
    after_wait = after;
}

struct Segment::Impl {
    std::string name;
    handle box;
    // The handle's checked field table, in the form codec.cpp takes.
    std::vector<FieldDesc> fields;
    std::vector<std::string> names;
    double lock_timeout = default_lock_timeout;
    std::atomic<bool> closed{false};
    std::unique_ptr<Notifier> notifier;
    // close() can race any other call on every build, since lock waits release the GIL and the
    // free-threaded build has none; it takes this exclusively.
    mutable std::shared_mutex lifetime;
    // The even seq hold_write_lock took the lock from, for release_held_lock.
    std::uint64_t held = 0;

    // Called once the handle is open, to take what the rest of this file reads from it.
    void bind() {
        fields.reserve(box.field_count());
        for (std::uint16_t i = 0; i < box.field_count(); ++i) {
            const field_spec &f = box.field(i);
            fields.push_back({f.offset, f.capacity, static_cast<FieldKind>(f.kind)});
        }
        static_cast<void>(box.set_lock_timeout(seconds(lock_timeout)));
        box.set_wait_hooks(before_wait, after_wait);
        auto *h = static_cast<header *>(box.base());
        notifier = std::make_unique<Notifier>(name, h->wake_word, h->waiters);
    }

    [[noreturn]] void fail(status code) const {
        if (code == status::lock_timeout)
            throw LockTimeout("box '" + name + "' is locked by pid " + std::to_string(box.writer_pid()) +
                              "; call force_unlock() if that process is gone");
        throw_status(code, name);
    }

    template <class T> T check(result<T> &&got) const {
        if (!got)
            fail(got.error());
        if constexpr (!std::is_void_v<T>)
            return *std::move(got);
    }

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
        check_index(index, fields.size());
        return fields[index];
    }

    // Every value's index is checked; the caller holds enter()'s guard.
    void write(std::span<const value> values) {
        const result<void> written = box.write(values, seconds(lock_timeout));
        // write checks the values too; this finds which one, for the message.
        if (!written && written.error() == status::range)
            check_values(fields, values);
        check(result<void>(written));
        notifier->wake_all();
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
    if (fields.empty() || fields.size() > max_fields)
        throw std::invalid_argument("a box needs between 1 and 256 fields");
    check_names(names, fields.size());
    if (record_size > std::numeric_limits<std::uint32_t>::max())
        throw std::invalid_argument("record_size " + std::to_string(record_size) + " is too large");
    std::vector<value> initial;
    initial.reserve(values.size());
    for (const auto &[index, bytes] : values) {
        // Checked before the narrowing cast, which would otherwise turn index 65536 into field 0.
        check_index(index, fields.size());
        initial.push_back({static_cast<std::uint16_t>(index), bytes_of(bytes)});
    }
    check_values(fields, initial);
    std::vector<field_spec> table;
    table.reserve(fields.size());
    for (const auto &f : fields)
        table.push_back(to_spec(f));

    auto impl = std::make_unique<Impl>();
    impl->name = name;
    impl->names = names;
    impl->lock_timeout = lock_timeout;
    result<handle> made = handle::create(name, table, static_cast<std::uint32_t>(record_size), schema_hash,
                                         default_waiter_slots, initial);
    impl->box = impl->check(std::move(made));
    impl->bind();
    return std::unique_ptr<Segment>(new Segment(std::move(impl)));
}

std::unique_ptr<Segment> Segment::attach(const std::string &name, const std::vector<std::string> &names,
                                         std::uint64_t schema_hash, double lock_timeout) {
    check_lock_timeout(lock_timeout);
    auto impl = std::make_unique<Impl>();
    impl->name = name;
    impl->names = names;
    impl->lock_timeout = lock_timeout;
    // A creator that has not published the box within 1 s is not coming back.
    result<handle> opened = handle::open(name, seconds((std::min)(lock_timeout, 1.0)));
    if (!opened && opened.error() == status::layout) {
        const result<header> seen = inspect(name);
        const std::string version =
            seen ? std::to_string(seen->layout_major) + "." + std::to_string(seen->layout_minor)
                 : std::string("another major version");
        throw SchemaMismatch("segment '" + name + "' uses layout " + version);
    }
    impl->box = impl->check(std::move(opened));
    if (impl->box.schema_hash() != schema_hash)
        throw SchemaMismatch("segment '" + name + "' was created by a different class");
    check_names(names, impl->box.field_count());
    impl->bind();
    return std::unique_ptr<Segment>(new Segment(std::move(impl)));
}

void Segment::read(std::uint32_t index, FieldRead &out) const {
    auto guard = impl_->enter();
    out.field = &impl_->field(index);
    const auto f = static_cast<std::uint16_t>(index);
    read_value got = impl_->check(impl_->box.read(f, std::as_writable_bytes(std::span(out.words))));
    if (got.len <= sizeof out.words) {
        out.bytes = {reinterpret_cast<const char *>(out.words), got.len};
    } else {
        do {
            out.large.resize(got.len);
            got = impl_->check(impl_->box.read(f, std::as_writable_bytes(std::span(out.large))));
        } while (got.len > out.large.size());
        out.bytes = {out.large.data(), got.len};
    }
    out.version = got.version;
}

std::unique_ptr<std::byte[]> Segment::read_record() const {
    auto guard = impl_->enter();
    const std::size_t size = impl_->box.record_size();
    // new[] without () leaves the bytes uninitialised; the read overwrites all of them.
    std::unique_ptr<std::byte[]> record(new std::byte[size]);
    impl_->check(impl_->box.read_record({record.get(), size}));
    return record;
}

std::uint32_t Segment::field_count() const { return static_cast<std::uint32_t>(impl_->fields.size()); }

std::string_view Segment::payload(std::uint32_t index, const std::byte *record) const {
    // From the Segment's own copy of the field, not through the handle, which a close() may have emptied
    // since read_record returned.
    const std::span<const std::byte> bytes = detail::payload(to_spec(impl_->field(index)), record);
    return {reinterpret_cast<const char *>(bytes.data()), bytes.size()};
}

void Segment::write(const std::vector<std::pair<std::uint32_t, std::string>> &values) {
    auto guard = impl_->enter();
    // A write of a few fields, the usual case, needs no allocation here.
    value few[8];
    std::vector<value> many;
    value *converted = few;
    if (values.size() > std::size(few)) {
        many.resize(values.size());
        converted = many.data();
    }
    for (std::size_t i = 0; i < values.size(); ++i) {
        // Checked before the narrowing cast, which would otherwise turn index 65536 into field 0.
        check_index(values[i].first, impl_->fields.size());
        converted[i] = {static_cast<std::uint16_t>(values[i].first), bytes_of(values[i].second)};
    }
    impl_->write({converted, values.size()});
}

void Segment::write_one(std::uint32_t index, std::string_view bytes) {
    auto guard = impl_->enter();
    check_index(index, impl_->fields.size());
    const value one{static_cast<std::uint16_t>(index), bytes_of(bytes)};
    impl_->write({&one, 1});
}

std::uint64_t Segment::version(std::uint32_t index) const {
    auto guard = impl_->enter();
    impl_->field(index);
    return impl_->box.version(static_cast<std::uint16_t>(index));
}

std::uint64_t Segment::generation() const {
    auto guard = impl_->enter();
    return impl_->box.generation();
}

std::uint64_t Segment::wait(std::uint64_t last_generation, double timeout) const {
    if (!(std::isfinite(timeout) && timeout >= 0 && timeout <= max_timeout))
        throw std::invalid_argument("timeout must be finite and in [0, 86400]");
    auto guard = impl_->enter();
    auto *h = static_cast<header *>(impl_->box.base());
    const Clock::time_point deadline =
        Clock::now() + std::chrono::duration_cast<Clock::duration>(seconds(timeout));
    for (;;) {
        // Read the word before the generation: a write landing in between changes the word, so the wait returns.
        const std::uint32_t word = detail::atomic(h->wake_word).load(std::memory_order_seq_cst);
        const std::uint64_t current = impl_->box.generation();
        if (current != last_generation)
            return current;
        const double remaining = std::chrono::duration<double>(deadline - Clock::now()).count();
        if (remaining <= 0)
            return current;
        impl_->notifier->wait(word, remaining);
    }
}

void Segment::force_unlock() {
    auto guard = impl_->enter();
    impl_->check(impl_->box.force_unlock());
}

void Segment::hold_write_lock() {
    auto guard = impl_->enter();
    impl_->held = impl_->check(impl_->box.lock(seconds(impl_->lock_timeout)));
}

void Segment::release_held_lock() {
    auto guard = impl_->enter();
    impl_->box.unlock(impl_->held);
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
    impl_->box = handle();
}

void Segment::unlink(const std::string &name) {
    const result<void> removed = sharedbox::unlink(name);
    const int error = errno;
    if (removed)
        return;
    if (removed.error() == status::not_found)
        throw SegmentMissing("no segment named '" + name + "'");
    if (removed.error() == status::os)
        throw std::system_error(error, std::generic_category(), "cannot unlink segment '" + name + "'");
    throw_status(removed.error(), name);
}

std::uint64_t Segment::size() const {
    auto guard = impl_->enter();
    return impl_->box.size();
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
