#pragma once

#include <Python.h>
#include <nanobind/nanobind.h>

#include <cstddef>
#include <memory>
#include <span>
#include <string>
#include <utility>
#include <vector>

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
    /// Sets the value of key, replacing it in place when the value holds key; raises as a write would, and
    /// ValueError past the capacity.
    void setitem(nb::handle key, nb::handle value);
    /// Removes key and its value, keeping the order of the other items; raises KeyError(key) when the value
    /// holds no such key.
    void delitem(nb::handle key);
    /// Removes key and returns its value, or returns fallback when the value holds no such key, or raises
    /// KeyError(key) when fallback is invalid. A decode that raises leaves the value as it was.
    nb::object pop(nb::handle key, nb::handle fallback);
    /// Removes the last item and returns it as a pair, raising KeyError when the value is empty. A decode
    /// that raises leaves the value as it was.
    nb::object popitem();
    /// The value of key, or when the value holds no such key, fallback after storing it as the value of key.
    nb::object setdefault(nb::handle key, nb::handle fallback);
    /// Sets the items of other, when valid, then of pairs, all of them or, when the new keys do not fit, none.
    /// other is a mapping when it has a keys method, and otherwise an iterable of pairs.
    void update(nb::handle other, nb::kwargs pairs);
    /// Removes every item.
    void remove_all();
    /// A new value of self's class holding the items of self updated by those of other, or of other updated
    /// by those of self when reflected, for other a collections.abc.Mapping; NotImplemented for anything else.
    /// Raises ValueError past the capacity.
    static nb::object merge(nb::pointer_and_handle<NativeDict> self, nb::handle other, bool reflected);

private:
    /// The value of key, or an invalid object when the value holds no such key. Encodes the key and decodes
    /// the value without the mutex.
    nb::object find_value(nb::handle key);
    /// Raises TypeError when value is a NativeDict that was never built.
    void refuse_unbuilt(nb::handle value) const;
    /// Appends to out the slot of key and value, each encoded as a write would encode it. Runs without the
    /// mutex.
    void encode_item(nb::handle key, nb::handle value, std::vector<std::byte> &out) const;
    /// Appends to out the slots of the items of other: those of a NativeDict of the same types, copied once its
    /// keys are checked, or those of a mapping, which has a keys method, or of an iterable of pairs, encoded.
    /// Runs without the mutex.
    void items_of(nb::handle other, std::vector<std::byte> &out) const;
    /// Sets every item of items in turn, all of them or, when the new keys do not fit, none; returns the count
    /// it would have had instead, or 0. Checks the keys first; the caller holds the mutex.
    std::size_t put_all(const std::vector<std::byte> &items);
    /// The slot holding key, as lookup finds it, with scanning set to whether lookups still scan, so that
    /// other slots may hold key too.
    std::uint32_t locate(const std::byte *key, bool &scanning);
    /// Removes slot at, which locate found, and when scanning every other slot holding key, keeping the order
    /// of the rest.
    void remove_at(std::uint32_t at, const std::byte *key, bool scanning);
};

} // namespace sharedbox
