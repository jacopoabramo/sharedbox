#pragma once

#include <Python.h>
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <type_traits>

#include <sharedbox/sharedbox.hpp>

#include "segment.hpp"

#if defined(_MSC_VER)
#define SBX_NOINLINE __declspec(noinline)
#else
#define SBX_NOINLINE [[gnu::noinline]]
#endif

namespace sharedbox {

class Types;

/// Sets a Python exception of type and throws it.
[[noreturn]] inline void raise(PyObject *type, const std::string &message) {
    PyErr_SetString(type, message.c_str());
    throw nanobind::python_error();
}

/// isinstance(value, type), throwing the error it raises.
inline bool instance(PyObject *value, PyObject *type) {
    const int found = PyObject_IsInstance(value, type);
    if (found < 0)
        throw nanobind::python_error();
    return found == 1;
}

/// made, a new reference from the C API, or the error it set thrown when it is nullptr.
inline PyObject *owned(PyObject *made) {
    if (made == nullptr)
        throw nanobind::python_error();
    return made;
}

// Holds size value-initialised entries: up to N in place, more on the heap. Only the entries in use are
// constructed, since MSVC constructs and destroys a whole array of a class type through a call per
// element, which costs more than the rest of a small update.
template <typename T, std::size_t N> class Scratch {
    // The constructor builds the entries outside any cleanup that would free the heap block.
    static_assert(std::is_nothrow_default_constructible_v<T>);

public:
    explicit Scratch(std::size_t size)
        : size_(size), data_(size <= N ? reinterpret_cast<T *>(local_) : std::allocator<T>().allocate(size)) {
        std::uninitialized_value_construct_n(data_, size_);
    }
    Scratch(const Scratch &) = delete;
    Scratch &operator=(const Scratch &) = delete;
    ~Scratch() {
        std::destroy_n(data_, size_);
        if (size_ > N)
            std::allocator<T>().deallocate(data_, size_);
    }
    T &operator[](std::size_t i) { return data_[i]; }
    std::span<T> span() { return {data_, size_}; }

private:
    alignas(T) std::byte local_[N * sizeof(T)];
    std::size_t size_;
    T *data_;
};

/// Room for the bytes of one encoded value: a number, a reference or a short bytes value fits in
/// place, a longer bytes value goes on the heap. The constructor leaves the bytes uninitialised.
struct EncodeBuffer {
    EncodeBuffer() noexcept {}
    EncodeBuffer(const EncodeBuffer &) = delete;
    EncodeBuffer &operator=(const EncodeBuffer &) = delete;

    /// n zeroed bytes, in place when they fit, else on the heap.
    std::byte *reserve(std::size_t n) {
        std::byte *out = small;
        if (n > sizeof small) {
            large.reset(new std::byte[n]);
            out = large.get();
        }
        std::memset(out, 0, n);
        return out;
    }

    alignas(std::uint64_t) std::byte small[sizeof(box_ref)];
    std::unique_ptr<std::byte[]> large;
    /// The array view a value's bytes point into, kept so a resizable source cannot move its memory
    /// before the write ends. Optional, so a write of any other kind skips the view's destructor.
    std::optional<nanobind::ndarray<nanobind::ro>> keep;
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
void encode_all(const Segment &s, const Types *types, std::span<const Pending> values,
                std::span<EncodeBuffer> buffers, std::span<value> out);

/// types, or a TypeError naming the field when a value of a kind above 5 has none to convert it.
const Types &need(const Types *types, std::uint32_t index);

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
