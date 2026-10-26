#pragma once

#include <Python.h>
#include <nanobind/nanobind.h>

#include <cstddef>
#include <memory>
#include <span>
#include <string>
#include <utility>

#include "keys.hpp"

namespace sharedbox {

/// A dict value: a KeyedStore whose slots hold a key and then its value, iterated in insertion order.
class NativeDict : public KeyedStore {
public:
    /// Takes the count and used slots of stored, the bytes of the dict node of types. Raises ValueError
    /// naming label when the count passes the capacity or the slots it names are missing.
    NativeDict(nb::object types, const detail::type_ref &node, std::string label,
               std::span<const std::byte> stored)
        : KeyedStore(std::move(types), node, std::move(label), stored, nullptr) {}
    /// Takes over the buffer stored, length bytes long, instead of copying it, checked as above.
    NativeDict(nb::object types, const detail::type_ref &node, std::string label,
               std::unique_ptr<std::byte[]> stored, std::size_t length)
        : KeyedStore(std::move(types), node, std::move(label), {stored.get(), length}, &stored) {}
    /// A new value of self's class holding the same items, copied without decoding one.
    static nb::object copy(nb::pointer_and_handle<NativeDict> self);
    /// The value of key, raising KeyError(key) when the value holds no such key.
    nb::object getitem(nb::handle key);
    /// The value of key, or fallback when the value holds no such key.
    nb::object get(nb::handle key, nb::handle fallback);
    /// Whether the value holds key; false for a key that cannot be encoded.
    bool contains(nb::handle key);
    /// Equality with a collections.abc.Mapping: the same keys, found by their bytes, with values equal as ==
    /// compares them; NotImplemented for anything else.
    nb::object equals(nb::handle other);

private:
    /// The value of key, or an invalid object when the value holds no such key. Encodes the key and decodes
    /// the value without the mutex.
    nb::object find_value(nb::handle key);
    /// Raises TypeError when value is a NativeDict that was never built.
    void refuse_unbuilt(nb::handle value) const;
};

} // namespace sharedbox
