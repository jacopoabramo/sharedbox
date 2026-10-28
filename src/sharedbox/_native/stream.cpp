#include "stream.hpp"

#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>
#include <sharedbox/sharedbox.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <memory>
#include <mutex>
#include <optional>
#include <span>
#include <stdexcept>
#include <string>
#include <system_error>
#include <unordered_map>
#include <utility>
#include <vector>

#include "codec.hpp"
#include "segment.hpp"
#include "types.hpp"

namespace nb = nanobind;
using namespace nb::literals;

namespace sharedbox {
namespace {

struct WouldBlock : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct EndOfStream : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct StreamBusy : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct StreamClosed : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct Interrupted : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct ReaderReleased : std::runtime_error {
    ReaderReleased(const std::string &what, std::uint64_t missed) : std::runtime_error(what), missed(missed) {}
    std::uint64_t missed;
};

/// A stream mapped in this process. The Stream object and every end made from it share it, so the mapping
/// outlives the ends. It holds no Python object: the Stream and each end own a reference to the Types
/// instance their values are converted with, so the collector sees every reference to it and can free a
/// cycle that runs through the item type. Each reference is visited by its own object's traverse.
struct StreamCore {
    stream s;
    std::string name;
};

template <class T> int traverse_slot(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self));
    if (!nb::inst_ready(self))
        return 0;
    return nb::inst_ptr<T>(self)->traverse(visit, arg);
}

template <class T> int clear_slot(PyObject *self) {
    if (nb::inst_ready(self))
        nb::inst_ptr<T>(self)->clear();
    return 0;
}

template <class T>
const PyType_Slot gc_slots[] = {{Py_tp_traverse, reinterpret_cast<void *>(traverse_slot<T>)},
                                {Py_tp_clear, reinterpret_cast<void *>(clear_slot<T>)},
                                {0, nullptr}};

[[noreturn]] void throw_stream(const error &e, const std::string &name) {
#ifdef _WIN32
    const std::error_category &category = std::system_category();
#else
    const std::error_category &category = std::generic_category();
#endif
    switch (e.code) {
    case status::exists:
        throw SegmentExists("a segment named '" + name + "' already exists");
    case status::not_found:
        throw SegmentMissing("no segment named '" + name + "'");
    case status::kind_mismatch:
        throw KindMismatch("'" + name + "' is a " + std::string(detail::kind_name(e.found)) + ", not a stream");
    case status::foreign:
        throw SchemaMismatch("segment '" + name +
                             "' was made by another version of sharedbox or is not a sharedbox segment");
    case status::layout:
        throw SchemaMismatch("stream '" + name + "' uses core version " + std::to_string(e.found >> 48) + "." +
                             std::to_string(e.found >> 32 & 0xFFFF) + " and stream layout " +
                             std::to_string(e.found >> 16 & 0xFFFF) + "." + std::to_string(e.found & 0xFFFF) +
                             "; this sharedbox reads core version " + std::to_string(core_major) +
                             " and stream layout " + std::to_string(stream_layout_major));
    case status::corrupt:
        throw SchemaMismatch("stream '" + name + "' has a corrupt header");
    case status::timeout:
        throw WouldBlock("stream '" + name + "': nothing to receive or no room to send");
    case status::ended:
        throw EndOfStream("stream '" + name + "' has ended");
    case status::interrupted:
        throw Interrupted("stream '" + name + "': the wait was interrupted");
    case status::no_slot:
        throw NoWaiterSlot("stream '" + name + "' has max_readers readers open, or no free waiter slot");
    case status::range:
        throw std::invalid_argument("stream '" + name + "': a size, capacity or name is out of range");
    case status::os:
        throw std::system_error(e.os, category, "stream '" + name + "'");
    default:
        throw std::runtime_error("stream '" + name + "': sharedbox.hpp returned " +
                                 std::to_string(static_cast<int>(e.code)));
    }
}

/// Ends a metadata read: a version the stream does not hold, reclaimed or never added, raises MetadataGone.
[[noreturn]] void fail_metadata(const error &e, const std::string &name, std::uint64_t version) {
    if (e.code == status::not_found)
        throw MetadataGone("stream '" + name + "' no longer holds metadata version " + std::to_string(version) +
                           ", or never did");
    throw_stream(e, name);
}

std::span<const std::byte> bytes_of(const nb::bytes &b) { return std::as_bytes(std::span(b.c_str(), b.size())); }

seconds wait_of(double timeout) {
    if (!(timeout >= 0.0) || timeout > 1.0)
        throw std::invalid_argument("a stream wait lasts 0 to 1 s at a time; got " + std::to_string(timeout));
    return seconds(timeout);
}

/// The bytes of the item's value: a str, bytes or Decimal value follows its u32 length; any other value
/// takes the whole item.
std::span<const std::byte> value_bytes(const detail::type_ref &t, std::span<const std::byte> item,
                                       const std::string &name) {
    if (!detail::prefixed(t.kind))
        return item;
    std::uint32_t length = 0;
    std::memcpy(&length, item.data(), sizeof length);
    if (length > item.size() - sizeof length)
        throw SchemaMismatch("stream '" + name + "' holds an item with a corrupt length");
    return item.subspan(sizeof length, length);
}

std::shared_ptr<StreamCore> make_core(stream s) {
    auto core = std::make_shared<StreamCore>();
    core->name = std::string(s.name());
    core->s = std::move(s);
    return core;
}

/// The calls an end makes are serialised by calls. A thread that holds the GIL only try-locks it and
/// releases the GIL before it waits for it, since a wait at the gate holds calls without the GIL; so a
/// wait never holds the GIL and a second thread waits its turn. interrupts guards interrupt against
/// close, which the header requires never overlap, and a reader's metadata lookup against close. Lock
/// order: the GIL, then interrupts, never the other way; calls is only waited for with the GIL released.
class End {
public:
    End(std::shared_ptr<StreamCore> core, nb::handle types)
        : name_(core->name), core_(std::move(core)), types_(nb::borrow(types)),
          item_types_(nb::cast<const Types *>(types)) {}
    bool closed() const { return closed_.load(); }

    int traverse(visitproc visit, void *arg) const {
        Py_VISIT(types_.ptr());
        return 0;
    }
    void clear() {
        item_types_ = nullptr;
        types_.release().dec_ref();
    }

protected:
    void check_open() const {
        if (forked_)
            throw StreamClosed("stream '" + name_ +
                               "': this end was opened before fork; open a new one in this process");
        if (closed_.load())
            throw StreamClosed("stream '" + name_ + "': this end is closed");
    }
    std::string name_;
    /// Ends a call that failed: a wait that close() interrupted is a closed end, not an interrupt.
    [[noreturn]] void fail(const error &e) const {
        if (e.code == status::interrupted && closed_.load())
            throw StreamClosed("stream '" + name_ + "': this end is closed");
        throw_stream(e, name_);
    }
    std::shared_ptr<StreamCore> core_;
    nb::object types_;
    /// The Types inside types_, found once, so a call casts nothing; null once the collector cleared the end.
    const Types *item_types_;
    /// The item type, which this end's own reference keeps alive.
    const Types &types() const {
        if (item_types_ == nullptr)
            throw StreamClosed("stream '" + name_ + "': this end is closed");
        return *item_types_;
    }
    // unique_ptr so after_fork can replace a mutex another thread of the parent held, rather than unlock it.
    std::unique_ptr<std::mutex> calls_ = std::make_unique<std::mutex>();
    std::unique_ptr<std::mutex> interrupts_ = std::make_unique<std::mutex>();
    std::atomic<bool> closed_{false};
    bool forked_ = false;

    void forget_locks_after_fork() {
        forked_ = true;
        // The parent's thread holding them does not exist here; the old mutexes are left, never unlocked.
        static_cast<void>(calls_.release());
        static_cast<void>(interrupts_.release());
        calls_ = std::make_unique<std::mutex>();
        interrupts_ = std::make_unique<std::mutex>();
    }
};

class Slot;

class Sender : public End {
public:
    Sender(std::shared_ptr<StreamCore> core, nb::handle types, stream_sender end)
        : End(std::move(core), types), end_(std::move(end)) {}
    ~Sender() { close(); }

    std::uint64_t send(nb::handle value, double timeout, bool release) {
        reserve(timeout, release);
        return commit(value);
    }

    void reserve(double timeout, bool release) {
        check_open();
        const seconds wait = wait_of(timeout);
        {
            // The usual case, no other reservation and room in the ring, takes the slot without giving up the GIL.
            std::unique_lock lock(*calls_, std::try_to_lock);
            if (lock && !reserved_ && !closed_.load()) {
                const result<std::span<std::byte>> got = end_.begin(seconds(0));
                if (got) {
                    hold(*got);
                    return;
                }
                if (got.error().code != status::timeout)
                    fail(got.error());
            }
        }
        std::optional<result<std::span<std::byte>>> got;
        {
            nb::gil_scoped_release unlocked;
            const detail::clock::time_point deadline =
                detail::clock::now() + std::chrono::duration_cast<detail::clock::duration>(wait);
            std::unique_lock lock(*calls_);
            free_->wait_until(lock, deadline, [&] { return !reserved_ || closed_.load(); });
            if (!closed_.load()) {
                if (reserved_) {
                    got.emplace(unexpected(status::timeout));
                } else {
                    const detail::clock::duration left =
                        (std::max)(deadline - detail::clock::now(), detail::clock::duration::zero());
                    got.emplace(end_.begin(std::chrono::duration_cast<seconds>(left),
                                           release ? on_full::release : on_full::wait));
                    if (*got)
                        hold(**got);
                }
            }
        }
        if (!got)
            check_open();
        if (!*got)
            fail(got->error());
    }

    std::uint64_t commit(nb::handle value) {
        check_open();
        if (slot_ == nullptr)
            throw std::runtime_error("stream '" + name_ + "': no slot is reserved");
        try {
            types().encode_to(0, value.ptr(), {slot_, core_->s.item_size()});
        } catch (...) {
            abandon();
            throw;
        }
        std::unique_lock lock = lock_calls();
        release_slot();
        if (closed_.load())
            throw StreamClosed("stream '" + name_ + "': this end is closed");
        const result<std::uint64_t> sent = end_.publish();
        if (!sent)
            fail(sent.error());
        return *sent;
    }

    void abandon() {
        std::unique_lock lock = lock_calls();
        end_.abandon();
        release_slot();
    }

    std::uint64_t add_metadata(const nb::bytes &doc) {
        check_open();
        // The add reads write_pos as the sender's only writer, so it must not overlap a send.
        std::unique_lock lock = lock_calls();
        if (closed_.load())
            throw StreamClosed("stream '" + name_ + "': this end is closed");
        const result<std::uint64_t> added = end_.add_metadata(bytes_of(doc));
        if (added)
            return *added;
        if (added.error().code == status::metadata_full) {
            if (static_cast<const stream_header *>(core_->s.base())->metadata == 0)
                throw MetadataFull("stream '" + name_ +
                                   "' has no metadata area; create it with metadata or a metadata_capacity");
            throw MetadataFull("stream '" + name_ +
                               "': an item still in the ring was sent under the metadata version this add "
                               "would reclaim; send more items first, or create the stream with a larger "
                               "metadata_versions or metadata_capacity");
        }
        if (added.error().code == status::range)
            throw std::invalid_argument("stream '" + name_ + "': the metadata document is " +
                                        std::to_string(doc.size()) +
                                        " bytes, more than its metadata area takes, about a third of "
                                        "metadata_capacity");
        fail(added.error());
    }

    void interrupt() {
        std::lock_guard lock(*interrupts_);
        if (forked_ || closed_.load())
            return;
        static_cast<void>(end_.interrupt());
    }

    void close() {
        {
            std::lock_guard lock(*interrupts_);
            if (closed_.exchange(true))
                return;
            // In a child of fork the slot is the parent's, and interrupting it would end the parent's wait.
            if (!forked_)
                static_cast<void>(end_.interrupt());
        }
        {
            nb::gil_scoped_release unlocked;
            std::lock_guard lock(*calls_);
            end_.close();
            free_->notify_all();
        }
    }

    void after_fork() {
        forget_locks_after_fork();
        // A thread of the parent may have been waiting on it; it is left, never notified.
        static_cast<void>(free_.release());
        free_ = std::make_unique<std::condition_variable>();
        reserved_ = false;
        slot_ = nullptr;
    }

private:
    friend class Slot;

    /// The item over the reserved slot, built from template_item; called by Slot only.
    nb::object take(nb::handle template_item) {
        check_open();
        if (slot_ == nullptr)
            throw std::runtime_error("stream '" + name_ + "': no slot is reserved");
        auto keep = std::make_unique<std::shared_ptr<StreamCore>>(core_);
        nb::capsule owner(keep.get(),
                          [](void *p) noexcept { delete static_cast<std::shared_ptr<StreamCore> *>(p); });
        // Released only once the capsule exists, so a failure to make it does not leak the copy.
        keep.release();
        return types().with_arrays(0, template_item.ptr(), slot_, owner);
    }
    std::unique_lock<std::mutex> lock_calls() {
        std::unique_lock lock(*calls_, std::try_to_lock);
        if (!lock) {
            nb::gil_scoped_release unlocked;
            lock.lock();
        }
        return lock;
    }
    /// Called with calls held.
    void hold(std::span<std::byte> slot) {
        reserved_ = true;
        slot_ = slot.data();
    }
    /// Called with calls held.
    void release_slot() {
        reserved_ = false;
        slot_ = nullptr;
        free_->notify_all();
    }

    stream_sender end_;
    /// Written with calls held; slot_ is read without it by the thread that holds the reservation. free_ is
    /// notified when a reservation ends and when the sender closes.
    bool reserved_ = false;
    std::byte *slot_ = nullptr;
    std::unique_ptr<std::condition_variable> free_ = std::make_unique<std::condition_variable>();
};

/// A slot a Sender reserved, from its reservation to its send or abandon: the Sender, and the item built
/// over the slot.
class Slot {
public:
    Slot(nb::handle sender, nb::handle template_item) : sender_(nb::borrow(sender)) {
        Sender &end = nb::cast<Sender &>(sender_);
        name_ = end.name_;
        item_ = end.take(template_item);
    }

    nb::object item() const {
        check_ready();
        return item_;
    }

    void send(nb::handle value) {
        check_ready();
        if (!open_.exchange(false))
            throw std::runtime_error("stream '" + name_ + "': this slot was already sent or given up");
        nb::cast<Sender &>(sender_).commit(value);
    }

    void abandon() {
        if (sender_.is_valid() && open_.exchange(false))
            nb::cast<Sender &>(sender_).abandon();
    }

    int traverse(visitproc visit, void *arg) const {
        Py_VISIT(sender_.ptr());
        Py_VISIT(item_.ptr());
        return 0;
    }
    void clear() {
        sender_.release().dec_ref();
        item_.release().dec_ref();
    }

private:
    /// Raises StreamClosed once the collector cleared the slot.
    void check_ready() const {
        if (!sender_.is_valid() || !item_.is_valid())
            throw StreamClosed("stream '" + name_ + "': this slot is closed");
    }
    nb::object sender_;
    nb::object item_;
    std::string name_;
    std::atomic<bool> open_{true};
};

class Reader : public End {
public:
    Reader(std::shared_ptr<StreamCore> core, nb::handle types, stream_reader end)
        : End(std::move(core), types), end_(std::move(end)) {}
    ~Reader() { close(); }

    nb::tuple receive(double timeout,
                      const std::vector<std::pair<std::vector<std::uint32_t>, nb::object>> &arrays) {
        check_open();
        const seconds wait = wait_of(timeout);
        const Types &types = this->types();
        // Each array the caller passed, sorted by where its bytes start in the item, so the copy below walks the
        // item once: the gaps go to scratch, each array's bytes straight into the array. Everything is checked
        // here, with the GIL held and before the wait, so a bad array consumes no item. The arguments keep
        // each array alive for the whole call.
        std::vector<Types::ArrayTarget> targets;
        // Made only when arrays are given, so a plain receive builds no map.
        std::optional<std::unordered_map<std::uint64_t, PyObject *>> given;
        if (!arrays.empty())
            given.emplace();
        nb::object whole;
        for (const auto &[path, out] : arrays) {
            Types::ArrayTarget &target = targets.emplace_back(types.array_target(0, path, out.ptr()));
            const bool fresh = path.empty() ? !std::exchange(whole, out).is_valid()
                                            : given->emplace(target.key, out.ptr()).second;
            if (!fresh)
                throw std::invalid_argument("stream '" + name_ + "': two arrays given for one member");
        }
        std::sort(targets.begin(), targets.end(),
                  [](const Types::ArrayTarget &a, const Types::ArrayTarget &b) { return a.offset < b.offset; });
        // Every max_array is a target too, so only the elements its shape line counts are copied.
        if (types.has_max_arrays())
            targets = types.item_targets(0, std::move(targets));
        const std::size_t size = core_->s.item_size();
        // An item of up to 4 KiB is copied to the stack; a larger one to the heap, which a bare array item
        // then keeps as its data instead of copying again.
        alignas(std::max_align_t) std::byte small[4096];
        std::unique_ptr<std::byte[]> large;
        std::byte *scratch = small;
        // ponytail: the scratch holds the whole item even when arrays take most of it, and its pages under the
        // arrays are never touched; a scratch of only the gaps is the upgrade if a measurement shows the
        // allocation.
        if (size > sizeof small) {
            large.reset(new std::byte[size]);
            scratch = large.get();
        }
        std::optional<result<received>> got;
        std::uint64_t released = 0;
        {
            nb::gil_scoped_release unlocked;
            std::lock_guard lock(*calls_);
            if (!closed_.load()) {
                const std::uint64_t before = end_.missed();
                got.emplace(end_.receive_with(
                    [&](std::span<const std::byte> item) noexcept {
                        std::size_t at = 0;
                        for (const Types::ArrayTarget &target : targets) {
                            std::memcpy(scratch + at, item.data() + at, target.offset - at);
                            at = target.offset + target.size;
                            if (target.max == nullptr) {
                                std::memcpy(target.view.data(), item.data() + target.offset, target.size);
                                continue;
                            }
                            // The count comes from the scratch copy of the line, which no sender writes, so a
                            // line overwritten during the copy cannot make it run past the span.
                            std::memcpy(scratch + target.offset, item.data() + target.offset, 64);
                            std::uint64_t n = types.stored_bytes(*target.max, scratch + target.offset);
                            if (n == UINT64_MAX)
                                n = 0;
                            std::byte *into = target.view.is_valid() ? static_cast<std::byte *>(target.view.data())
                                                                     : scratch + target.offset + 64;
                            std::memcpy(into, item.data() + target.offset + 64, n);
                        }
                        std::memcpy(scratch + at, item.data() + at, size - at);
                    },
                    wait));
                released = end_.missed() - before;
                missed_total_.store(end_.missed(), std::memory_order_relaxed);
            }
        }
        if (!got)
            check_open();
        if (!*got) {
            if (got->error().code == status::released)
                throw ReaderReleased("stream '" + name_ +
                                         "': the sender released this lossless reader, which lost " +
                                         std::to_string(released) + " items",
                                     released);
            if (got->error().code == status::corrupt)
                throw SchemaMismatch("stream '" + name_ +
                                     "': the sender overwrote an item this lossless reader had not read");
            fail(got->error());
        }
        const detail::type_ref &t = types.field(0);
        const std::span<const std::byte> bytes = value_bytes(t, {scratch, size}, name_);
        if (whole.is_valid())
            return nb::make_tuple(types.out_view(t, whole.ptr(), bytes.data(), name_), (*got)->position);
        nb::object value = !given || given->empty()
                               ? types.decode(0, bytes, large && arrays.empty() ? &large : nullptr)
                               : types.decode_given(0, bytes, *given);
        return nb::make_tuple(value, (*got)->position);
    }

    std::uint64_t metadata_version_at(std::uint64_t position) {
        // Close sets closed_ under interrupts before it closes the end, and a receive changes nothing the lookup
        // reads, so the lookup need not wait for a receive in another thread.
        std::lock_guard lock(*interrupts_);
        check_open();
        const result<std::uint64_t> found = end_.metadata_version_at(position);
        if (found)
            return *found;
        if (found.error().code != status::not_found)
            fail(found.error());
        // Version 1 starts the ring and takes the first entry, so any add that reclaims a version reclaims it
        // first: while it is held no version is gone, and the item came before every version.
        const stream &s = core_->s;
        if (s.metadata_head() == 0 || s.metadata_length(1))
            return 0;
        throw MetadataGone("stream '" + name_ + "' no longer holds the metadata version of the item at position " +
                           std::to_string(position));
    }

    std::uint32_t mode() const { return static_cast<std::uint32_t>(end_.mode()); }
    std::uint64_t missed() const { return missed_total_.load(std::memory_order_relaxed); }

    void interrupt() {
        std::lock_guard lock(*interrupts_);
        if (forked_ || closed_.load())
            return;
        static_cast<void>(end_.interrupt());
    }

    void close() {
        {
            std::lock_guard lock(*interrupts_);
            if (closed_.exchange(true))
                return;
            if (!forked_)
                static_cast<void>(end_.interrupt());
        }
        {
            nb::gil_scoped_release unlocked;
            std::lock_guard lock(*calls_);
            end_.close();
        }
    }

    void after_fork() { forget_locks_after_fork(); }

private:
    stream_reader end_;
    // end_.missed() is only read under calls_; this copy lets a polling thread read it without waiting.
    std::atomic<std::uint64_t> missed_total_{0};
};

class Stream {
public:
    static Stream create(const std::string &name, nb::handle types, std::uint32_t entry, std::uint64_t schema_hash,
                         std::uint64_t capacity, std::uint32_t max_readers,
                         const std::optional<nb::bytes> &metadata, std::uint64_t metadata_capacity,
                         std::uint32_t metadata_versions) {
        const std::string &table = nb::cast<const Types &>(types).table();
        const metadata_spec spec{metadata ? bytes_of(*metadata) : std::span<const std::byte>(), metadata_capacity,
                                 metadata_versions};
        check_metadata_spec("stream '" + name + "'", spec);
        result<stream> made =
            stream::create(name, {reinterpret_cast<const std::byte *>(table.data()), table.size()}, entry,
                           capacity, max_readers, schema_hash, spec);
        if (!made)
            throw_stream(made.error(), name);
        return Stream(make_core(std::move(*made)), types);
    }

    static Stream attach(const std::string &name, nb::handle types, std::uint32_t entry,
                         std::uint64_t schema_hash) {
        std::optional<result<stream>> opened;
        {
            nb::gil_scoped_release unlocked;
            // A creator that has not published the stream within 1 s is not coming back.
            opened.emplace(stream::open(name, seconds(1.0)));
        }
        if (!*opened)
            throw_stream(opened->error(), name);
        const std::string &table = nb::cast<const Types &>(types).table();
        const std::span<const std::byte> stored = (*opened)->types_table();
        const bool same_table =
            stored.size() == table.size() && std::memcmp(stored.data(), table.data(), table.size()) == 0;
        if ((*opened)->schema_hash() != schema_hash || !same_table ||
            static_cast<const stream_header *>((*opened)->base())->item_entry != entry)
            throw SchemaMismatch("stream '" + name + "' holds another item type");
        return Stream(make_core(std::move(**opened)), types);
    }

    const std::string &name() const { return core().name; }
    std::uint64_t capacity() const { return core().s.capacity(); }
    std::uint32_t max_readers() const { return core().s.max_readers(); }
    std::uint64_t create_id() const { return core().s.create_id(); }
    bool closed() const { return core_ == nullptr; }
    std::uint64_t metadata_head() const { return core().s.metadata_head(); }

    std::uint32_t metadata_length(std::uint64_t version) const {
        const result<std::uint32_t> length = core().s.metadata_length(version);
        if (!length)
            fail_metadata(length.error(), core().name, version);
        return *length;
    }

    nb::bytes read_metadata(std::uint64_t version) const {
        std::string out(metadata_length(version), char{});
        const result<std::uint32_t> read = core().s.read_metadata(version, std::as_writable_bytes(std::span(out)));
        if (!read)
            fail_metadata(read.error(), core().name, version);
        return nb::bytes(out.data(), out.size());
    }

    nb::tuple statistics() const {
        const stream &s = core().s;
        std::vector<reader_info> found(s.max_readers());
        const std::size_t open = s.readers(found);
        nb::list readers;
        for (std::size_t i = 0; i < open && i < found.size(); ++i)
            readers.append(
                nb::make_tuple(found[i].position, static_cast<std::uint32_t>(found[i].mode), found[i].pid));
        return nb::make_tuple(s.write_position(), s.ended(), s.sender_pid(), readers);
    }

    std::unique_ptr<Sender> sender() {
        result<stream_sender> made = core().s.sender();
        if (!made && made.error().code == status::busy)
            throw StreamBusy("stream '" + core().name + "' has a sender in process " +
                             std::to_string(core().s.sender_pid()));
        if (!made)
            throw_stream(made.error(), core().name);
        return std::make_unique<Sender>(core_, types(), std::move(*made));
    }

    std::unique_ptr<Reader> reader(std::uint32_t mode, bool newest) {
        if (mode < 1 || mode > 3)
            throw std::invalid_argument("mode is 1 lossless, 2 lossy or 3 latest");
        result<stream_reader> made =
            core().s.reader(static_cast<read_mode>(mode), newest ? start_at::newest : start_at::oldest);
        if (!made)
            throw_stream(made.error(), core().name);
        return std::make_unique<Reader>(core_, types(), std::move(*made));
    }

    void close() { core_.reset(); }

    int traverse(visitproc visit, void *arg) const {
        Py_VISIT(types_.ptr());
        return 0;
    }
    void clear() { types_.release().dec_ref(); }

private:
    Stream(std::shared_ptr<StreamCore> core, nb::handle types)
        : core_(std::move(core)), types_(nb::borrow(types)) {}
    nb::handle types() const {
        if (!types_.is_valid())
            throw StreamClosed("this stream is closed");
        return types_;
    }
    const StreamCore &core() const {
        if (core_ == nullptr)
            throw StreamClosed("this stream is closed");
        return *core_;
    }
    StreamCore &core() {
        if (core_ == nullptr)
            throw StreamClosed("this stream is closed");
        return *core_;
    }
    std::shared_ptr<StreamCore> core_;
    nb::object types_;
};

} // namespace

void bind_stream(nb::module_ &m) {
    nb::exception<WouldBlock>(m, "WouldBlock");
    nb::exception<EndOfStream>(m, "EndOfStream");
    nb::exception<StreamBusy>(m, "StreamBusyError", PyExc_RuntimeError);
    nb::exception<StreamClosed>(m, "StreamClosedError", PyExc_ValueError);
    nb::exception<Interrupted>(m, "_Interrupted");
    nb::exception<ReaderReleased> released(m, "ReaderReleased");
    // Registered after the exception, so it is tried first and can set the attribute the plain translator cannot.
    nb::register_exception_translator(
        [](const std::exception_ptr &p, void *type) {
            try {
                std::rethrow_exception(p);
            } catch (const ReaderReleased &e) {
                nb::object kind = nb::borrow(static_cast<PyObject *>(type));
                nb::object instance = kind(e.what());
                instance.attr("missed") = e.missed;
                PyErr_SetObject(kind.ptr(), instance.ptr());
            }
        },
        released.ptr());

    nb::class_<Sender>(m, "Sender", nb::type_slots(gc_slots<Sender>))
        .def("send", &Sender::send, "value"_a.none(), "timeout"_a, "release"_a = false)
        .def("reserve", &Sender::reserve, "timeout"_a, "release"_a = false)
        .def("commit", &Sender::commit, "value"_a.none())
        .def("abandon", &Sender::abandon)
        .def("add_metadata", &Sender::add_metadata, "doc"_a)
        .def("interrupt", &Sender::interrupt)
        .def("close", &Sender::close)
        .def("_after_fork", &Sender::after_fork)
        .def_prop_ro("closed", &Sender::closed);
    nb::class_<Slot>(m, "Slot", nb::is_generic(), nb::type_slots(gc_slots<Slot>))
        .def(nb::init<nb::handle, nb::handle>(), "sender"_a, "template"_a.none())
        .def_prop_ro("item", &Slot::item)
        .def("send", &Slot::send, "item"_a.none())
        .def("abandon", &Slot::abandon);
    nb::class_<Reader>(m, "Reader", nb::type_slots(gc_slots<Reader>))
        .def("receive", &Reader::receive, "timeout"_a,
             "arrays"_a = std::vector<std::pair<std::vector<std::uint32_t>, nb::object>>{})
        .def_prop_ro("mode", &Reader::mode)
        .def_prop_ro("missed", &Reader::missed)
        .def("metadata_version_at", &Reader::metadata_version_at, "position"_a)
        .def("interrupt", &Reader::interrupt)
        .def("close", &Reader::close)
        .def("_after_fork", &Reader::after_fork)
        .def_prop_ro("closed", &Reader::closed);
    nb::class_<Stream>(m, "Stream", nb::type_slots(gc_slots<Stream>))
        .def_static("create", &Stream::create, "name"_a, "types"_a, "entry"_a, "schema_hash"_a, "capacity"_a,
                    "max_readers"_a, "metadata"_a = nb::none(), "metadata_capacity"_a = 0,
                    "metadata_versions"_a = 64)
        .def_static("attach", &Stream::attach, "name"_a, "types"_a, "entry"_a, "schema_hash"_a)
        .def_prop_ro("name", &Stream::name, nb::lock_self())
        .def_prop_ro("capacity", &Stream::capacity, nb::lock_self())
        .def_prop_ro("max_readers", &Stream::max_readers, nb::lock_self())
        .def_prop_ro("create_id", &Stream::create_id, nb::lock_self())
        .def_prop_ro("closed", &Stream::closed, nb::lock_self())
        .def("metadata_head", &Stream::metadata_head, nb::lock_self())
        .def("metadata_length", &Stream::metadata_length, "version"_a, nb::lock_self())
        .def("read_metadata", &Stream::read_metadata, "version"_a, nb::lock_self())
        .def("statistics", &Stream::statistics, nb::lock_self())
        .def("sender", &Stream::sender, nb::lock_self())
        .def("reader", &Stream::reader, "mode"_a, "newest"_a, nb::lock_self())
        .def("close", &Stream::close, nb::lock_self());
}

} // namespace sharedbox
