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

/// A set value: a KeyedStore whose elements are its keys, iterated in slot order.
class NativeSet : public KeyedStore {
public:
    /// Takes the count and used slots of stored, the bytes of the set node of types. Raises ValueError
    /// naming label when the count passes the capacity or the slots it names are missing.
    NativeSet(nb::object types, const detail::type_ref &node, std::string label, std::span<const std::byte> stored)
        : KeyedStore(std::move(types), node, std::move(label), stored, nullptr) {}
    /// Takes over the buffer stored, length bytes long, instead of copying it, checked as above.
    NativeSet(nb::object types, const detail::type_ref &node, std::string label,
              std::unique_ptr<std::byte[]> stored, std::size_t length)
        : KeyedStore(std::move(types), node, std::move(label), {stored.get(), length}, &stored) {}
    /// A new value of self's class holding the same elements, copied without decoding one.
    static nb::object copy(nb::pointer_and_handle<NativeSet> self);
};

} // namespace sharedbox
