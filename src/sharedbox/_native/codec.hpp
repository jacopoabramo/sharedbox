#pragma once

#include <Python.h>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <utility>

#include "segment.hpp"

namespace sharedbox {

/// The bytes stored for value; raises a Python exception naming the field.
std::string encode(const FieldDesc &field, const std::string &name, PyObject *value);

/// A new reference to the value stored in data.
PyObject *decode(const FieldDesc &field, const char *data, std::size_t size);

/// One value to encode: the field index and the Python object, borrowed.
struct Pending {
    std::uint32_t field;
    PyObject *value;
};

/// Encodes every value of values into out, which holds as many entries; checks each before
/// returning, so a caller that writes only after it returns writes all or nothing. A bad value
/// raises its Python exception as a C++ exception.
void encode_all(const Segment &s, std::span<const Pending> values,
                std::span<std::pair<std::uint32_t, std::string>> out);

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
