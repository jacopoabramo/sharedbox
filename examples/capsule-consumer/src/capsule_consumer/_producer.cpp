// Producer: a C++ thread that reads its rate and count from a Settings box and sends Sample items into a
// stream, both taken from Python objects with sharedbox/python.hpp. The thread never calls into Python.
#include <nanobind/nanobind.h>
#include <sharedbox/python.hpp>
#include <sharedbox/sharedbox.hpp>

#include <array>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <span>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>

namespace nb = nanobind;
using namespace nb::literals;

namespace {

// The schema hashes of Settings and Sample, which the package passes in at import.
std::uint64_t settings_schema = 0;
std::uint64_t sample_schema = 0;

// The bytes of one Sample item: its fields in declaration order, as the stream stores them.
struct Item {
    std::int64_t index;
    double value;
};
static_assert(sizeof(Item) == 16);

constexpr std::uint16_t rate_field = 0;
constexpr std::uint16_t count_field = 1;

std::string status_text(sharedbox::status code) {
    static constexpr std::array<const char *, 19> names{
        "ok",          "exists",        "not_found",     "layout",     "schema",
        "corrupt",     "lock_timeout",  "timeout",       "no_slot",    "range",
        "os",          "kind_mismatch", "foreign",       "busy",       "ended",
        "interrupted", "released",      "metadata_full", "unsupported"};
    const int index = -static_cast<int>(code);
    if (index >= 0 && index < static_cast<int>(names.size()))
        return names[static_cast<std::size_t>(index)];
    return "status " + std::to_string(static_cast<int>(code));
}

sharedbox::handle take_settings(nb::handle obj) {
    if (settings_schema == 0)
        throw std::runtime_error("capsule_consumer was not imported");
    std::optional<sharedbox::handle> box = sharedbox::python::take_box(obj.ptr(), settings_schema);
    if (!box)
        throw nb::python_error();
    // The hash says the box is a Settings; this says the C++ code reads its fields as Settings declares.
    const std::array<std::pair<std::uint16_t, std::uint8_t>, 2> expected{
        {{rate_field, sharedbox::kind_float}, {count_field, sharedbox::kind_int}}};
    if (box->field_count() != expected.size())
        throw nb::type_error("Settings has another number of fields than this extension reads");
    for (const auto &[field, kind] : expected) {
        if (box->field(field).kind != kind)
            throw nb::type_error(("Settings field " + std::to_string(field) + " is kind " +
                                  std::to_string(box->field(field).kind) + ", expected kind " +
                                  std::to_string(kind))
                                     .c_str());
    }
    return std::move(*box);
}

sharedbox::stream take_samples(nb::handle obj) {
    std::optional<sharedbox::stream> stream = sharedbox::python::take_stream(obj.ptr(), sample_schema);
    if (!stream)
        throw nb::python_error();
    if (stream->item_size() != sizeof(Item))
        throw nb::type_error(("Sample items are " + std::to_string(stream->item_size()) + " bytes, expected " +
                              std::to_string(sizeof(Item)))
                                 .c_str());
    return std::move(*stream);
}

sharedbox::stream_sender claim_sender(sharedbox::stream &stream) {
    sharedbox::result<sharedbox::stream_sender> sender = stream.sender();
    if (!sender)
        throw std::runtime_error("cannot send on the stream: " + status_text(sender.error().code));
    return std::move(*sender);
}

class Producer {
public:
    Producer(nb::handle settings, nb::handle stream)
        : box_(take_settings(settings)), stream_(take_samples(stream)), sender_(claim_sender(stream_)) {}

    Producer(const Producer &) = delete;
    Producer &operator=(const Producer &) = delete;

    ~Producer() {
        // Only the thread runs without Python, so the join needs no release of the GIL.
        if (std::thread thread = halt(); thread.joinable())
            thread.join();
    }

    void start() {
        std::lock_guard lock(mutex_);
        if (thread_.joinable() || stopping_ || closed_)
            throw std::runtime_error(stopping_ || closed_ ? "the producer has stopped"
                                                          : "the producer is already running");
        thread_ = std::thread([this] { run(); });
    }

    void stop() {
        std::thread thread = halt();
        if (thread.joinable()) {
            nb::gil_scoped_release release;
            thread.join();
        }
        std::string error;
        {
            std::lock_guard lock(mutex_);
            error = std::exchange(error_, {});
        }
        if (!error.empty())
            throw std::runtime_error(error);
    }

private:
    // Asks the thread to stop, ends a send it waits in, and hands over the thread for the caller to join.
    std::thread halt() {
        std::thread thread;
        {
            std::lock_guard lock(mutex_);
            stopping_ = true;
            // interrupt must not overlap close, which the thread calls under the same lock.
            if (!closed_)
                static_cast<void>(sender_.interrupt());
            thread = std::move(thread_);
        }
        wake_.notify_all();
        return thread;
    }

    void run() {
        send_until_done();
        std::lock_guard lock(mutex_);
        sender_.close();
        closed_ = true;
    }

    void send_until_done() {
        for (std::int64_t i = 0;; ++i) {
            double rate_hz = 0;
            std::int64_t count = 0;
            if (!read(rate_field, rate_hz) || !read(count_field, count))
                return;
            if (!(rate_hz > 0) || !std::isfinite(rate_hz))
                return fail("rate_hz must be positive and finite, got " + std::to_string(rate_hz));
            if (count > 0 && i >= count)
                return;
            const Item item{i, static_cast<double>(i) * 0.5};
            const auto sent = sender_.send(std::as_bytes(std::span{&item, 1}), sharedbox::seconds(1.0));
            std::unique_lock lock(mutex_);
            if (!sent) {
                if (!(sent.error().code == sharedbox::status::interrupted && stopping_))
                    fail_locked("send failed: " + status_text(sent.error().code));
                return;
            }
            if (wake_.wait_for(lock, std::chrono::duration<double>(1.0 / rate_hz), [this] { return stopping_; }))
                return;
        }
    }

    template <class T> bool read(std::uint16_t field, T &out) {
        std::array<std::byte, sizeof(T)> bytes;
        const auto got = box_.read(field, bytes);
        if (!got) {
            fail("reading Settings failed: " + status_text(got.error().code));
            return false;
        }
        std::memcpy(&out, bytes.data(), sizeof(T));
        return true;
    }

    void fail(std::string message) {
        std::lock_guard lock(mutex_);
        fail_locked(std::move(message));
    }

    void fail_locked(std::string message) {
        if (error_.empty())
            error_ = std::move(message);
    }

    sharedbox::handle box_;
    // Declared before sender_, which uses its state and so is destroyed first.
    sharedbox::stream stream_;
    sharedbox::stream_sender sender_;
    // Guards everything below, and the sender's interrupt and close.
    std::mutex mutex_;
    std::condition_variable wake_;
    std::thread thread_;
    bool stopping_ = false;
    bool closed_ = false;
    std::string error_;
};

} // namespace

NB_MODULE(_producer, m) {
    m.def(
        "set_schemas",
        [](std::uint64_t settings, std::uint64_t sample) {
            settings_schema = settings;
            sample_schema = sample;
        },
        "settings"_a, "sample"_a, "Keep the schema hashes of Settings and Sample, which Producer checks.");

    nb::class_<Producer>(m, "Producer",
                         "Send Sample items into a stream from a C++ thread, at the rate a Settings box sets.\n\n"
                         "Raises TypeError for a box that is not a Settings or a stream of another item "
                         "type, and RuntimeError when another sender holds the stream.")
        .def(nb::init<nb::handle, nb::handle>(), "settings"_a, "stream"_a)
        .def("start", &Producer::start, "Start the thread; RuntimeError if it runs or has stopped.")
        .def("stop", &Producer::stop,
             "Stop the thread and wait for it; RuntimeError with the error that ended it, if any.")
        .def(
            "__enter__",
            [](Producer &producer) -> Producer & {
                producer.start();
                return producer;
            },
            nb::rv_policy::reference)
        .def(
            "__exit__", [](Producer &producer, nb::handle, nb::handle, nb::handle) { producer.stop(); },
            nb::arg().none(), nb::arg().none(), nb::arg().none());
}
