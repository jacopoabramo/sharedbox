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

/// The set operators: |, &, - and ^.
enum class set_op { join, common, difference, symmetric };

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
    /// A new value of self's class holding self op other, or other op self when reflected, for other a
    /// collections.abc.Set; NotImplemented for anything else. An element of other that is not a key raises
    /// when it would be in the result, and is left out otherwise. Raises ValueError past the capacity.
    static nb::object combine(nb::pointer_and_handle<NativeSet> self, nb::handle other, set_op op, bool reflected);
    /// Changes self to self op other and returns it, or NotImplemented as combine does; past the capacity
    /// raises ValueError and leaves self as it was.
    static nb::object combine_in_place(nb::pointer_and_handle<NativeSet> self, nb::handle other, set_op op);
    /// The comparison op, Py_LE, Py_LT, Py_GE or Py_GT, of the value with a collections.abc.Set, as set
    /// orders sets; NotImplemented for anything else.
    nb::object compare(nb::handle other, int op);
    /// Whether the value holds none of the elements of iterable.
    bool isdisjoint(nb::handle iterable);

private:
    /// Raises TypeError when value is a NativeSet that was never built.
    void refuse_unbuilt(nb::handle value) const;
    /// Appends the elements of iterable to out, each encoded as encode_key encodes it; with quiet, leaves
    /// out each that is not a key and returns false when there was one. Runs without the mutex.
    bool encode_keys(nb::handle iterable, std::vector<std::byte> &out, bool quiet) const;
    /// Fills the empty out with the keys of other: the slots of a NativeSet of the same elements, copied once
    /// its keys are checked, or the elements of any other iterable, encoded and returned as encode_keys encodes
    /// and returns them. Runs without the mutex.
    bool keys_of(nb::handle other, std::vector<std::byte> &out, bool quiet) const;
    /// Marks in found, resized to the count, each slot holding one of keys, and appends to extra, when not
    /// null, each key no slot holds; returns how many no slot holds. Checks the keys first; the caller
    /// holds the mutex.
    std::size_t match(const std::vector<std::byte> &keys, std::vector<bool> &found, std::vector<std::byte> *extra);
    /// Appends each of keys the value does not hold, all of them or, when they do not fit, none; returns
    /// the count it would have had instead, or 0. The caller holds the mutex and has checked the keys.
    std::size_t append_new(const std::vector<std::byte> &keys);
    /// Changes the value to the value op keys, or keys op the value when reflected, all or nothing; returns
    /// what append_new returns. Takes the mutex.
    std::size_t apply(const std::vector<std::byte> &keys, set_op op, bool reflected);
    /// Raises TypeError for a NativeSet other that was never built; returns false when other is not a
    /// collections.abc.Set.
    bool takes(nb::handle other) const;
};

} // namespace sharedbox
