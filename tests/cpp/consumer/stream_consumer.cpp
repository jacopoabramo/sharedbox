// A C++ library that takes a stream out of a "sharedbox_stream" capsule; tests/test_capsule.py loads it with
// ctypes.
#include <sharedbox/sharedbox.hpp>

#include <cstdint>
#include <new>
#include <span>

#ifdef _WIN32
#define CONSUMER_API extern "C" __declspec(dllexport)
#else
#define CONSUMER_API extern "C" __attribute__((visibility("default")))
#endif

namespace {

int code_of(const sharedbox::error &e) { return static_cast<int>(e.code); }

} // namespace

// Takes the stream of the capsule's handle; nullptr, with the status in *code, if that fails.
CONSUMER_API void *consumer_take_stream(sbx_handle *capsule_handle, int *code) {
    sharedbox::result<sharedbox::stream> taken = sharedbox::stream::from_capsule(capsule_handle);
    if (!taken) {
        *code = code_of(taken.error());
        return nullptr;
    }
    auto *owned = new (std::nothrow) sharedbox::stream(std::move(*taken));
    *code = owned == nullptr ? static_cast<int>(sharedbox::status::os) : 0;
    return owned;
}

// Claims the sender, sends count items value, value + 1, ..., and closes the sender, which ends the stream.
CONSUMER_API int consumer_send_ints(void *s, std::int64_t value, std::uint32_t count, double timeout) {
    auto &stream = *static_cast<sharedbox::stream *>(s);
    sharedbox::result<sharedbox::stream_sender> sender = stream.sender();
    if (!sender)
        return code_of(sender.error());
    if (sender->item_size() != sizeof(std::int64_t))
        return static_cast<int>(sharedbox::status::range);
    for (std::uint32_t i = 0; i < count; ++i) {
        const std::int64_t item = value + i;
        const auto sent = sender->send(std::as_bytes(std::span{&item, 1}), sharedbox::seconds(timeout));
        if (!sent)
            return code_of(sent.error());
    }
    return 0;
}

// Opens a lossless reader at the oldest item the ring holds and receives count items into out.
CONSUMER_API int consumer_receive_ints(void *s, std::int64_t *out, std::uint32_t count, double timeout) {
    auto &stream = *static_cast<sharedbox::stream *>(s);
    sharedbox::result<sharedbox::stream_reader> reader =
        stream.reader(sharedbox::read_mode::lossless, sharedbox::start_at::oldest);
    if (!reader)
        return code_of(reader.error());
    for (std::uint32_t i = 0; i < count; ++i) {
        const auto got =
            reader->receive(std::as_writable_bytes(std::span{out + i, 1}), sharedbox::seconds(timeout));
        if (!got)
            return code_of(got.error());
    }
    return 0;
}

CONSUMER_API void consumer_release_stream(void *s) { delete static_cast<sharedbox::stream *>(s); }
