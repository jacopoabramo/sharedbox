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
    /// Whether the value holds value; false for a value that cannot be encoded as a key.
    bool contains(nb::handle value);
    /// Adds value unless it is held; raises as a write would, and ValueError past the capacity.
    void add(nb::handle value);
    /// Removes value when it is held.
    void discard(nb::handle value);
    /// Removes value, raising KeyError(value) when it is not held.
    void remove(nb::handle value);
    /// Removes the last slot's element and returns it, raising KeyError when the value is empty. A decode
    /// that raises leaves the value as it was.
    nb::object pop();
    void remove_all();
    /// Adds the elements of every iterable, all of them or, when the distinct new ones do not fit, none.
    void update(nb::args iterables);
    /// Equality with a collections.abc.Set, key by key; NotImplemented for anything else.
    nb::object equals(nb::handle other);

private:
    /// Raises TypeError when value is a NativeSet that was never built.
    void refuse_unbuilt(nb::handle value) const;
};

} // namespace sharedbox
