#pragma once

#include <Python.h>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <span>
#include <string>

#include <sharedbox/sharedbox.hpp>

#include "segment.hpp"

namespace sharedbox {

/// Room for the bytes of one encoded value: a number, a reference or a short bytes value fits in
/// place, a longer bytes value goes on the heap. The constructor leaves the bytes uninitialised.
struct EncodeBuffer {
    EncodeBuffer() noexcept {}
    EncodeBuffer(const EncodeBuffer &) = delete;
    EncodeBuffer &operator=(const EncodeBuffer &) = delete;

    alignas(std::uint64_t) std::byte small[sizeof(box_ref)];
    std::unique_ptr<std::byte[]> large;
};

/// The bytes stored for value, held in buffer, in constant data, or for a str in value itself, so
/// they stay valid while value and buffer live; raises a Python exception naming the field.
std::span<const std::byte> encode(const FieldDesc &field, const std::string &name, PyObject *value,
                                  EncodeBuffer &buffer);

/// A new reference to the value stored in data.
PyObject *decode(const FieldDesc &field, const char *data, std::size_t size);

/// One value to encode: the field index and the Python object, borrowed.
struct Pending {
    std::uint32_t field;
    PyObject *value;
};

/// Encodes every value of values into out, using the buffer of the same position; buffers and out
/// hold as many entries as values. Checks each value before returning, so a caller that writes only
/// after it returns writes all or nothing. A bad value or field index raises a C++ exception.
void encode_all(const Segment &s, std::span<const Pending> values, std::span<EncodeBuffer> buffers,
                std::span<value> out);

/// Reads the whole record and passes it to use; a record of up to 4 KiB, the usual size, is read
/// into the stack, so the read allocates nothing.
template <typename Use> decltype(auto) with_record(const Segment &s, Use &&use) {
    alignas(std::max_align_t) std::byte small[4096];
    std::unique_ptr<std::byte[]> large;
    const std::size_t size = s.record_size();
    std::byte *record = small;
    if (size > sizeof small) {
        // new[] without () leaves the bytes uninitialised; the read overwrites all of them.
        large.reset(new std::byte[size]);
        record = large.get();
    }
    s.read_record({record, size});
    return use(static_cast<const std::byte *>(record));
}

} // namespace sharedbox
