#include "segment.hpp"

#include <sharedbox/sharedbox.hpp>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <iterator>
#include <limits>
#include <memory>
#include <mutex>
#include <new>
#include <optional>
#include <shared_mutex>
#include <span>
#include <system_error>
#include <thread>

#if defined(_MSC_VER)
#define SEGMENT_NOINLINE __declspec(noinline)
#else
#define SEGMENT_NOINLINE __attribute__((noinline))
#endif

namespace sharedbox {
namespace {

void check_lock_timeout(double lock_timeout) {
    if (!(std::isfinite(lock_timeout) && lock_timeout > 0 && lock_timeout <= max_timeout))
        throw std::invalid_argument("lock_timeout must be finite and in (0, 86400]");
}

void check_values(const std::vector<FieldDesc> &fields, std::span<const value> values) {
    for (const value &v : values) {
        check_index(v.field, fields.size());
        const FieldDesc &f = fields[v.field];
        const bool shorter_ok = is_prefixed(f.kind) || detail::is_collection(static_cast<std::uint32_t>(f.kind));
        if (shorter_ok ? v.bytes.size() > f.capacity : v.bytes.size() != f.capacity)
            throw std::invalid_argument("value for field " + std::to_string(v.field) + " is " +
                                        std::to_string(v.bytes.size()) + " bytes; the field holds " +
                                        std::to_string(f.capacity));
    }
}

field_spec to_spec(const FieldDesc &f) { return {f.offset, f.capacity, static_cast<std::uint8_t>(f.kind)}; }

std::span<const std::byte> bytes_of(std::string_view s) { return std::as_bytes(std::span(s.data(), s.size())); }

WaitHook before_wait = []() -> void * { return nullptr; };
ResumeHook after_wait = [](void *) {};

// The message for a creator whose pid cannot be checked from here, or nullopt when it can.
std::optional<std::string> foreign_creator(const header &h, const std::string &taken) {
#ifndef _WIN32
    // A pid means something only inside its namespace: with either namespace unknown (0), or a
    // real mismatch, there is nothing safe to say about whether the creator is still running.
    const std::uint64_t own_pidns = detail::current_pidns();
    if (h.creator_pidns == 0 || own_pidns == 0)
        return taken;
    if (h.creator_pidns != own_pidns)
        return taken + "; it was created in another pid namespace, such as another container";
#else
    static_cast<void>(h);
    static_cast<void>(taken);
#endif
    return std::nullopt;
}

// The header of a box whose creator has not published it yet. The creator fields are written before
// magic, but this reads them without waiting for magic and without the lock, so a creator still
// writing them can only make the message less precise.
std::optional<header> unpublished_header(const std::string &name) {
    detail::backoff no_wait(0);
    result<detail::os_mapping> map = detail::map_open(name, no_wait);
    if (!map)
        return std::nullopt;
    header copy;
    std::memcpy(&copy, map->base(), sizeof copy);
    return copy;
}

// The Python layer labels fields "Class.field", so the first label names the class to unlink through.
std::string exists_message(const std::string &name, const std::vector<std::string> &names) {
    const std::string taken = "a segment named '" + name + "' already exists";
#ifndef _WIN32
    const std::string::size_type dot = names.empty() ? std::string::npos : names[0].rfind('.');
    const std::string unlink = dot == std::string::npos ? "unlink('" + name + "') on its class"
                                                        : names[0].substr(0, dot) + ".unlink('" + name + "')";
#else
    static_cast<void>(names);
#endif
    const result<header> seen = inspect(name);
    if (!seen) {
        if (seen.error() != status::not_found)
            return taken;
        // Zero creator fields mean shared memory made by other software.
        if (const std::optional<header> creating = unpublished_header(name);
            creating && creating->creator_pid != 0) {
            if (std::optional<std::string> foreign = foreign_creator(*creating, taken))
                return *foreign;
            if (detail::process_alive(creating->creator_pid, creating->creator_start))
                return taken + "; it is being created by pid " + std::to_string(creating->creator_pid) +
                       ", which is still running; wait for it or use another name";
        }
#ifdef _WIN32
        // A Windows name exists only while some process holds a handle to it, and it may name an
        // object of another kind, so there is nothing to remove.
        return taken + "; it holds no published box, and a process (possibly this one) still has it open; "
                       "the name is freed when every handle to it is closed";
#else
        return taken + "; it holds no published box, so it may be left over from a crash during create, and " +
               unlink + " removes it";
#endif
    }
    if (!detail::major_ok(seen->layout_major))
        return taken;
    if (std::optional<std::string> foreign = foreign_creator(*seen, taken))
        return *foreign;
    if (detail::process_alive(seen->creator_pid, seen->creator_start))
        return taken + "; its creator, pid " + std::to_string(seen->creator_pid) + ", is still running";
#ifdef _WIN32
    return taken + "; its creator, pid " + std::to_string(seen->creator_pid) +
           ", is no longer running, but a process (possibly this one) still has it open; the name is freed "
           "when every handle to it is closed";
#else
    return taken + "; its creator, pid " + std::to_string(seen->creator_pid) +
           ", is no longer running, so it is probably left over from a crash, and " + unlink + " removes it";
#endif
}

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

std::string_view payload(const FieldDesc &field, const std::byte *record) {
    const std::span<const std::byte> bytes = detail::payload(to_spec(field), record);
    return {reinterpret_cast<const char *>(bytes.data()), bytes.size()};
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
    std::size_t record_size = 0;
    double lock_timeout = default_lock_timeout;
    std::atomic<bool> closed{false};
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
        record_size = box.record_size();
        static_cast<void>(box.set_lock_timeout(seconds(lock_timeout)));
        box.set_wait_hooks(before_wait, after_wait);
    }

    [[noreturn]] void fail(status code) const {
        if (code == status::lock_timeout)
            throw LockTimeout("box '" + name + "' is locked by pid " + std::to_string(box.writer_pid()) +
                              "; call force_unlock() if that process is gone");
        if (code == status::no_slot)
            throw NoWaiterSlot("all " + std::to_string(box.waiter_slots()) + " waiter slots of box '" + name +
                               "' are in use");
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

    // A described field: a list, set or dict copies its used slots, a large value runs the wait hooks
    // around its copy, anything else is read whole.
    void read_described(std::uint16_t f, FieldRead &out) const {
        const FieldDesc &field = fields[f];
        const std::size_t size = field.capacity;
        std::byte *buf = reinterpret_cast<std::byte *>(out.words);
        // An array is read straight into the heap buffer its decoded value then owns.
        if (size > sizeof out.words || static_cast<std::uint8_t>(field.kind) == kind_array) {
            out.large.reset(new std::byte[size]);
            buf = out.large.get();
        }
        read_value got{};
        if (detail::is_collection(static_cast<std::uint32_t>(field.kind)))
            got = check(box.read_used(f, {buf, size}));
        else if (size >= detail::large_copy)
            got = check(box.read_large(f, {buf, size}));
        else
            got = check(box.read(f, {buf, size}));
        out.bytes = {reinterpret_cast<const char *>(buf), got.len};
        out.version = got.version;
    }

    // A value longer than out.words, read again at its stored length until it fits. A function of its
    // own: inlined into Segment::read, this loop made MSVC pass the int path's values through the stack.
    SEGMENT_NOINLINE void read_long(std::uint16_t f, std::size_t len, FieldRead &out) const {
        read_value got{len, 0};
        std::size_t size = 0;
        do {
            size = got.len;
            out.large.reset(new std::byte[size]);
            got = check(box.read(f, {out.large.get(), size}));
        } while (got.len > size);
        out.bytes = {reinterpret_cast<const char *>(out.large.get()), got.len};
        out.version = got.version;
    }

    // Every value's index is checked; the caller holds enter()'s guard.
    void write(std::span<const value> values) {
        std::size_t total = 0;
        for (const value &v : values)
            total += v.bytes.size();
        const result<void> written = total >= detail::large_copy ? box.write_large(values, seconds(lock_timeout))
                                                                 : box.write(values, seconds(lock_timeout));
        // write checks the values too; this finds which one, for the message.
        if (!written && written.error() == status::range)
            check_values(fields, values);
        check(result<void>(written));
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
                                         std::uint16_t waiter_slots,
                                         const std::vector<std::pair<std::uint32_t, std::string>> &values,
                                         const std::string &types_table, std::uint16_t major, bool publish) {
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
    // A described field's capacity here is its description's offset, not its size; the header checks it.
    for (const value &v : initial)
        if (const auto kind = static_cast<std::uint32_t>(fields[v.field].kind);
            kind <= kind_ref || detail::prefixed(kind))
            check_values(fields, {&v, 1});
    std::vector<field_spec> table;
    table.reserve(fields.size());
    for (const auto &f : fields)
        table.push_back(to_spec(f));

    auto impl = std::make_unique<Impl>();
    impl->name = name;
    impl->names = names;
    impl->lock_timeout = lock_timeout;
    const auto size = static_cast<std::uint32_t>(record_size);
    result<handle> made = detail::create_impl(name, table, size, schema_hash, waiter_slots, initial,
                                              bytes_of(types_table), major, publish);
    if (!made && made.error() == status::exists)
        throw SegmentExists(exists_message(name, names));
    impl->box = impl->check(std::move(made));
    impl->bind();
    return std::unique_ptr<Segment>(new Segment(std::move(impl)));
}

std::unique_ptr<Segment> Segment::attach(const std::string &name, const std::vector<std::string> &names,
                                         std::uint64_t schema_hash, double lock_timeout,
                                         const std::string *types_table) {
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
    if (types_table != nullptr) {
        const std::span<const std::byte> stored = impl->box.types_table();
        if (stored.size() != types_table->size() ||
            std::memcmp(stored.data(), types_table->data(), stored.size()) != 0)
            throw SchemaMismatch("segment '" + name + "' was created by a class whose field types differ");
    }
    check_names(names, impl->box.field_count());
    // The header opens a field of a kind it does not know; this module can neither convert nor
    // check its value.
    for (std::uint16_t i = 0; i < impl->box.field_count(); ++i)
        if (!detail::kind_known(impl->box.field(i).kind))
            throw SchemaMismatch("segment '" + name + "' has a field of kind " +
                                 std::to_string(impl->box.field(i).kind) + ", which this version cannot read");
    impl->bind();
    return std::unique_ptr<Segment>(new Segment(std::move(impl)));
}

void Segment::read(std::uint32_t index, FieldRead &out) const {
    auto guard = impl_->enter();
    out.field = &impl_->field(index);
    const auto f = static_cast<std::uint16_t>(index);
    if (detail::described(static_cast<std::uint32_t>(out.field->kind))) [[unlikely]]
        return impl_->read_described(f, out);
    const read_value got = impl_->check(impl_->box.read(f, std::as_writable_bytes(std::span(out.words))));
    if (got.len > sizeof out.words) [[unlikely]]
        return impl_->read_long(f, got.len, out);
    out.bytes = {reinterpret_cast<const char *>(out.words), got.len};
    out.version = got.version;
}

std::size_t Segment::record_size() const { return impl_->record_size; }

void Segment::read_record(std::span<std::byte> out) const {
    auto guard = impl_->enter();
    if (out.size() >= detail::large_copy)
        impl_->check(impl_->box.read_record_large(out));
    else
        impl_->check(impl_->box.read_record(out));
}

std::uint32_t Segment::field_count() const { return static_cast<std::uint32_t>(impl_->fields.size()); }

void Segment::write(std::span<const value> values) {
    auto guard = impl_->enter();
    impl_->write(values);
}

void Segment::write_one(std::uint32_t index, std::span<const std::byte> bytes) {
    auto guard = impl_->enter();
    check_index(index, impl_->fields.size());
    const value one{static_cast<std::uint16_t>(index), bytes};
    impl_->write({&one, 1});
}

std::uint64_t Segment::version(std::uint32_t index) const {
    auto guard = impl_->enter();
    impl_->field(index);
    return impl_->box.version(static_cast<std::uint16_t>(index));
}

std::vector<std::uint64_t> Segment::versions() const {
    auto guard = impl_->enter();
    std::vector<std::uint64_t> out(impl_->fields.size());
    for (std::size_t i = 0; i < out.size(); ++i)
        out[i] = impl_->box.version(static_cast<std::uint16_t>(i));
    return out;
}

bool Segment::published() const {
    auto guard = impl_->enter();
    return detail::atomic(static_cast<header *>(impl_->box.base())->magic).load(std::memory_order_acquire) ==
           magic;
}

void Segment::publish() {
    auto guard = impl_->enter();
    if (!impl_->box.publish())
        throw std::invalid_argument("box '" + impl_->name + "' is already published");
}

std::uint64_t Segment::generation() const {
    auto guard = impl_->enter();
    return impl_->box.generation();
}

std::uint64_t Segment::wait(std::uint64_t last_generation, double timeout,
                            std::optional<std::uint16_t> slot) const {
    if (!(std::isfinite(timeout) && timeout >= 0 && timeout <= max_timeout))
        throw std::invalid_argument("timeout must be finite and in [0, 86400]");
    auto guard = impl_->enter();
    const std::uint16_t held = slot ? *slot : impl_->check(impl_->box.register_waiter());
    const result<wake> woken = impl_->box.wait(held, last_generation, seconds(timeout));
    if (!slot)
        impl_->box.release_waiter(held);
    if (!woken && woken.error() == status::range)
        throw std::invalid_argument("waiter slot " + std::to_string(held) + " is not held by this box");
    if (!woken && woken.error() != status::timeout)
        impl_->fail(woken.error());
    return impl_->box.generation();
}

std::uint16_t Segment::register_waiter() {
    auto guard = impl_->enter();
    return impl_->check(impl_->box.register_waiter());
}

void Segment::release_waiter(std::uint16_t slot) {
    auto guard = impl_->enter();
    impl_->box.release_waiter(slot);
}

void Segment::interrupt(std::uint16_t slot) {
    auto guard = impl_->enter();
    if (slot >= impl_->box.waiter_slots())
        throw std::invalid_argument("waiter slot " + std::to_string(slot) + " is out of range");
    impl_->check(impl_->box.interrupt(slot));
}

bool Segment::waiter_held(std::uint16_t slot) const {
    auto guard = impl_->enter();
    return impl_->box.waiter_held(slot);
}

std::uint32_t Segment::waiters() const {
    auto guard = impl_->enter();
    return impl_->box.waiters();
}

std::uint64_t Segment::create_id() const {
    auto guard = impl_->enter();
    return impl_->box.create_id();
}

sbx_handle *Segment::export_handle() const {
    auto guard = impl_->enter();
    sbx_handle *out = std::move(impl_->check(impl_->box.duplicate())).to_capsule();
    if (out == nullptr)
        throw std::bad_alloc();
    return out;
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

std::uint16_t Segment::major_version() const {
    auto guard = impl_->enter();
    return impl_->box.major_version();
}

std::uint16_t Segment::minor_version() const {
    auto guard = impl_->enter();
    return impl_->box.minor_version();
}

bool Segment::closed() const { return impl_->closed; }

const std::string &Segment::name() const { return impl_->name; }

double Segment::lock_timeout() const { return impl_->lock_timeout; }

std::span<const FieldDesc> Segment::fields() const { return impl_->fields; }

std::span<const std::string> Segment::field_names() const { return impl_->names; }

} // namespace sharedbox
