#include "stream.hpp"

#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>
#include <sharedbox/sharedbox.hpp>

#include <algorithm>
#include <atomic>
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

/// The calls an end makes are serialised by calls, taken after the GIL is released, so a wait never holds
/// the GIL and a second thread waits its turn. interrupts guards interrupt against close, which the header
/// requires never overlap. Lock order: the GIL, then interrupts, never the other way; calls is only taken
/// with the GIL released.
class End {
public:
    End(std::shared_ptr<StreamCore> core, nb::handle types)
        : name_(core->name), core_(std::move(core)), types_(nb::borrow(types)) {}
    bool closed() const { return closed_.load(); }

    int traverse(visitproc visit, void *arg) const {
        Py_VISIT(types_.ptr());
        return 0;
    }
    void clear() { types_.release().dec_ref(); }

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
    /// The item type, which this end's own reference keeps alive; empty once the collector cleared the end.
    const Types &types() const {
        if (!types_.is_valid())
            throw StreamClosed("stream '" + name_ + "': this end is closed");
        return nb::cast<const Types &>(types_);
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

class Sender : public End {
public:
    Sender(std::shared_ptr<StreamCore> core, nb::handle types, stream_sender end)
        : End(std::move(core), types), end_(std::move(end)) {}
    ~Sender() { close(); }

    std::uint64_t send(nb::handle value, double timeout) {
        check_open();
        const seconds wait = wait_of(timeout);
        const Types &types = this->types();
        EncodeBuffer buffer;
        const std::span<const std::byte> bytes = types.encode(0, value.ptr(), buffer);
        const bool prefixed = detail::prefixed(types.field(0).kind);
        std::optional<result<std::uint64_t>> sent;
        {
            nb::gil_scoped_release unlocked;
            std::lock_guard lock(*calls_);
            if (!closed_.load())
                sent.emplace(end_.send_with(
                    [&](std::span<std::byte> slot) noexcept {
                        std::byte *at = slot.data();
                        if (prefixed) {
                            const auto length = static_cast<std::uint32_t>(bytes.size());
                            std::memcpy(at, &length, sizeof length);
                            at += sizeof length;
                        }
                        std::memcpy(at, bytes.data(), bytes.size());
                    },
                    wait));
        }
        if (!sent)
            check_open();
        if (!*sent)
            fail(sent->error());
        return **sent;
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
        }
    }

    void after_fork() { forget_locks_after_fork(); }

private:
    stream_sender end_;
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
        std::unordered_map<std::uint64_t, PyObject *> given;
        nb::object whole;
        for (const auto &[path, out] : arrays) {
            Types::ArrayTarget &target = targets.emplace_back(types.array_target(0, path, out.ptr()));
            const bool fresh =
                path.empty() ? !std::exchange(whole, out).is_valid() : given.emplace(target.key, out.ptr()).second;
            if (!fresh)
                throw std::invalid_argument("stream '" + name_ + "': two arrays given for one member");
        }
        std::sort(targets.begin(), targets.end(),
                  [](const Types::ArrayTarget &a, const Types::ArrayTarget &b) { return a.offset < b.offset; });
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
        {
            nb::gil_scoped_release unlocked;
            std::lock_guard lock(*calls_);
            if (!closed_.load()) {
                got.emplace(end_.receive_with(
                    [&](std::span<const std::byte> item) noexcept {
                        std::size_t at = 0;
                        for (const Types::ArrayTarget &target : targets) {
                            std::memcpy(scratch + at, item.data() + at, target.offset - at);
                            std::memcpy(target.view.data(), item.data() + target.offset, target.size);
                            at = target.offset + target.size;
                        }
                        std::memcpy(scratch + at, item.data() + at, size - at);
                    },
                    wait));
                missed_total_.store(end_.missed(), std::memory_order_relaxed);
            }
        }
        if (!got)
            check_open();
        if (!*got) {
            if (got->error().code == status::corrupt)
                throw SchemaMismatch("stream '" + name_ +
                                     "': the sender overwrote an item this lossless reader had not read");
            fail(got->error());
        }
        const detail::type_ref &t = types.field(0);
        const std::span<const std::byte> bytes = value_bytes(t, {scratch, size}, name_);
        if (whole.is_valid())
            return nb::make_tuple(whole, (*got)->position);
        nb::object value = given.empty() ? types.decode(0, bytes, large && targets.empty() ? &large : nullptr)
                                         : types.decode_given(0, bytes, given);
        return nb::make_tuple(value, (*got)->position);
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
                         std::uint64_t capacity, std::uint32_t max_readers) {
        const std::string &table = nb::cast<const Types &>(types).table();
        result<stream> made =
            stream::create(name, {reinterpret_cast<const std::byte *>(table.data()), table.size()}, entry,
                           capacity, max_readers, schema_hash);
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
    std::uint64_t item_size() const { return core().s.item_size(); }
    std::uint64_t create_id() const { return core().s.create_id(); }
    bool closed() const { return core_ == nullptr; }

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

    nb::class_<Sender>(m, "Sender", nb::type_slots(gc_slots<Sender>))
        .def("send", &Sender::send, "value"_a.none(), "timeout"_a)
        .def("interrupt", &Sender::interrupt)
        .def("close", &Sender::close)
        .def("_after_fork", &Sender::after_fork)
        .def_prop_ro("closed", &Sender::closed);
    nb::class_<Reader>(m, "Reader", nb::type_slots(gc_slots<Reader>))
        .def("receive", &Reader::receive, "timeout"_a,
             "arrays"_a = std::vector<std::pair<std::vector<std::uint32_t>, nb::object>>{})
        .def_prop_ro("mode", &Reader::mode)
        .def_prop_ro("missed", &Reader::missed)
        .def("interrupt", &Reader::interrupt)
        .def("close", &Reader::close)
        .def("_after_fork", &Reader::after_fork)
        .def_prop_ro("closed", &Reader::closed);
    nb::class_<Stream>(m, "Stream", nb::type_slots(gc_slots<Stream>))
        .def_static("create", &Stream::create, "name"_a, "types"_a, "entry"_a, "schema_hash"_a, "capacity"_a,
                    "max_readers"_a)
        .def_static("attach", &Stream::attach, "name"_a, "types"_a, "entry"_a, "schema_hash"_a)
        .def_prop_ro("name", &Stream::name, nb::lock_self())
        .def_prop_ro("capacity", &Stream::capacity, nb::lock_self())
        .def_prop_ro("max_readers", &Stream::max_readers, nb::lock_self())
        .def_prop_ro("item_size", &Stream::item_size, nb::lock_self())
        .def_prop_ro("create_id", &Stream::create_id, nb::lock_self())
        .def_prop_ro("closed", &Stream::closed, nb::lock_self())
        .def("statistics", &Stream::statistics, nb::lock_self())
        .def("sender", &Stream::sender, nb::lock_self())
        .def("reader", &Stream::reader, "mode"_a, "newest"_a, nb::lock_self())
        .def("close", &Stream::close, nb::lock_self());
}

} // namespace sharedbox
