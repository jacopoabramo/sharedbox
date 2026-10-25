#pragma once

#include <Python.h>
#include <nanobind/nanobind.h>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <span>
#include <string>

#include "types.hpp"

namespace sharedbox {

namespace nb = nanobind;

/// A list value in its stored form: the count and the slots it uses, copied out of a record and owned
/// here, with the Types that converts its elements. Each method holds the object's mutex on the
/// free-threaded build and decodes or encodes one element at a time.
class NativeCollection {
public:
    /// Takes the count and used slots of stored, the bytes of the list node list of types. Raises
    /// ValueError naming label when the count passes the capacity or the slots it names are missing.
    NativeCollection(nb::object types, const detail::type_ref &list, std::string label,
                     std::span<const std::byte> stored);
    NativeCollection(const NativeCollection &) = delete;
    NativeCollection &operator=(const NativeCollection &) = delete;

    /// A new instance of cls, NativeCollection or a Python subclass of it, built as the constructor builds.
    static nb::object make(nb::handle cls, nb::object types, const detail::type_ref &list, std::string label,
                           std::span<const std::byte> stored);

    std::size_t size() const;
    /// Element index, counted from the end when negative; raises IndexError outside the value.
    nb::object get(Py_ssize_t index) const;
    /// Replaces element index after checking value as a write to the field would; raises IndexError
    /// outside the value, and leaves the element as it was when value does not fit.
    void set(Py_ssize_t index, nb::handle value);
    /// Element index, or an invalid object past the end.
    nb::object next(std::size_t index) const;
    /// The count and used slots, as a list field stores them.
    nb::bytes stored() const;

    int traverse(visitproc visit, void *arg) const;
    void clear();

private:
    static constexpr std::size_t no_position = static_cast<std::size_t>(-1);
    /// The slot index, counted from the end when negative, or no_position outside the value. The caller
    /// holds the mutex.
    std::size_t position(Py_ssize_t index) const;
    /// Raises IndexError for index; the caller does not hold the mutex.
    [[noreturn]] void out_of_range(Py_ssize_t index) const;
    /// Element index, or an invalid object outside the value.
    nb::object element(Py_ssize_t index) const;
    const Types &codec() const;

    nb::object types_;
    const Types *codec_ = nullptr;
    detail::type_ref list_;
    std::string label_;
    std::uint32_t slots_ = 0;
    std::uint32_t stride_ = 0;
    /// The count, also kept in the first four bytes of data_ so stored() hands them back as they are.
    std::uint32_t count_ = 0;
    /// The stored form: the count, then room for the used slots.
    std::unique_ptr<std::byte[]> data_;
    mutable nb::ft_mutex mutex_;
};

} // namespace sharedbox
