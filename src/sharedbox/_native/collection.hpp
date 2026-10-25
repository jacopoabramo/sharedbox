#pragma once

#include <Python.h>
#include <nanobind/nanobind.h>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <utility>
#include <vector>

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
    /// Element index, counted from the end when negative, or for a slice a new value of self's class
    /// holding those elements; raises IndexError outside the value.
    static nb::object getitem(nb::pointer_and_handle<NativeCollection> self, nb::handle index);
    /// A new value of self's class holding the same elements, copied without decoding one.
    static nb::object copy(nb::pointer_and_handle<NativeCollection> self);
    /// A copy of self extended with the elements of other, a list or a NativeCollection; NotImplemented
    /// for anything else, as list + refuses it.
    static nb::object add(nb::pointer_and_handle<NativeCollection> self, nb::handle other);
    /// A new value of self's class holding its elements times times over, or none for times below 1;
    /// NotImplemented when times is not an integer.
    static nb::object multiply(nb::pointer_and_handle<NativeCollection> self, nb::handle times);
    /// Repeats the elements in place times times over, or removes them for times below 1.
    void repeat(nb::handle times);
    /// The element annotation of the list node, as the layout gave it.
    nb::object element_annotation() const;
    std::uint32_t capacity() const { return capacity_; }
    /// Replaces element index, or the elements of a slice with those of an iterable, after checking each
    /// as a write to the field would. A slice of step 1 may change the length within the capacity; one of
    /// another step takes as many elements as it covers. Leaves the value as it was on any error.
    void setitem(nb::handle index, nb::handle value);
    /// Removes element index or the elements of a slice.
    void delitem(nb::handle index);
    /// Inserts value before index, clamped to the value as list.insert clamps it; an index past the range of
    /// Py_ssize_t raises OverflowError, as for list.
    void insert(nb::handle index, nb::handle value);
    void append(nb::handle value);
    /// Appends the elements of iterable, all of them or, when one does not fit, none.
    void extend(nb::handle iterable);
    /// Removes element index and returns it, raising IndexError with list.pop's messages. A decode that
    /// raises leaves the value as it was.
    nb::object pop(nb::handle index);
    /// Removes the first element equal to value, raising ValueError when there is none.
    void remove(nb::handle value);
    /// The first position from start to stop, clamped as list.index clamps them, holding an element equal
    /// to value; raises ValueError when there is none.
    Py_ssize_t index(nb::handle value, nb::handle start, nb::handle stop) const;
    std::size_t count(nb::handle value) const;
    bool contains(nb::handle value) const;
    /// Reverses the slots in place without decoding an element.
    void reverse();
    void remove_all();
    /// Equality with a list or another NativeCollection, element by element; NotImplemented otherwise.
    nb::object equals(nb::handle other) const;
    /// Element index, or an invalid object outside the value.
    nb::object element(Py_ssize_t index) const;
    /// The count and used slots, as a list field stores them.
    nb::bytes stored() const;
    /// Whether the elements have the schema text and annotation given.
    bool has_elements(const std::string &text, PyObject *annotation) const;
    /// Copies the stored form to out and returns its size. Raises ValueError naming name when the count
    /// passes capacity, before copying anything.
    std::size_t copy_to(std::byte *out, std::uint32_t capacity, const std::string &name) const;

    int traverse(visitproc visit, void *arg) const;
    void clear();

private:
    /// A slice's start, stop and step as it gives them, before they are fitted to a count.
    struct Slice {
        Py_ssize_t start = 0;
        Py_ssize_t stop = 0;
        Py_ssize_t step = 1;
    };
    /// The start, stop and step of slice; may run its __index__ methods, so the caller does not hold the
    /// mutex.
    static Slice unpack(nb::handle slice);
    static constexpr std::size_t no_position = static_cast<std::size_t>(-1);
    /// The slot index, counted from the end when negative, or no_position outside the value. The caller
    /// holds the mutex.
    std::size_t position(Py_ssize_t index) const;
    /// Raises IndexError for index; the caller does not hold the mutex.
    [[noreturn]] void out_of_range(Py_ssize_t index) const;
    /// Raises the ValueError of a mutation that would leave length elements.
    [[noreturn]] void too_many(std::size_t length) const;
    /// Raises the ValueError of a repetition of count elements times times over, whose product may not
    /// fit a std::size_t.
    [[noreturn]] void too_many(std::size_t count, Py_ssize_t times) const;
    /// Whether count elements times times over pass the capacity; times is at least 1.
    bool too_many_times(std::size_t count, Py_ssize_t times) const {
        return count != 0 && static_cast<std::size_t>(times) > capacity_ / count;
    }
    /// The first slots_ bytes of a stored form holding count elements: the count and the padding before the
    /// first slot. The caller holds the mutex.
    std::vector<std::byte> header(std::size_t count) const;
    /// A new value of self's class holding stored, with self's Types and label.
    static nb::object like(nb::pointer_and_handle<NativeCollection> self, std::span<const std::byte> stored);
    /// A new value of self's class holding its elements times times over, or none for times below 1.
    static nb::object repeated(nb::pointer_and_handle<NativeCollection> self, Py_ssize_t times);
    /// Replaces element index; the caller does not hold the mutex.
    void set(Py_ssize_t index, nb::handle value);
    /// Replaces the elements a slice of a step other than 1 covers with the elements of value.
    void set_stepped(const Slice &slice, nb::handle value);
    /// Inserts value before index, clamped to the count.
    void insert_at(Py_ssize_t index, nb::handle value);
    /// The first position from start to stop holding an element equal to value, or -1.
    Py_ssize_t find(nb::handle value, Py_ssize_t start, Py_ssize_t stop) const;
    /// The elements of iterable encoded one after another, raising the capacity error once base and the
    /// elements passed so far exceed the capacity.
    std::vector<std::byte> encode_all(nb::handle iterable, std::size_t base) const;
    /// Replaces the slots from start to stop, which the caller has clamped to the count, with the n
    /// encoded slots of encoded; returns the count it would have had instead when that passes the
    /// capacity. The caller holds the mutex.
    std::size_t splice(std::size_t start, std::size_t stop, const std::byte *encoded, std::size_t n);
    /// Stores count as the count; the caller holds the mutex.
    void set_count(std::size_t count);
    std::byte *slot(std::size_t at) const { return data_.get() + slots_ + at * stride_; }
    /// slice fitted to the current count, and the number of elements it covers; the caller holds the mutex.
    std::pair<Slice, std::size_t> fit(Slice slice) const;
    const Types &codec() const;

    nb::object types_;
    const Types *codec_ = nullptr;
    detail::type_ref list_;
    std::string label_;
    std::uint32_t slots_ = 0;
    std::uint32_t stride_ = 0;
    /// The count, also kept in the first four bytes of data_ so stored() hands them back as they are.
    std::uint32_t count_ = 0;
    std::uint32_t capacity_ = 0;
    /// The number of slots data_ has room for, grown by doubling up to the capacity.
    std::uint32_t room_ = 0;
    /// The stored form: the count, then room for room_ slots.
    std::unique_ptr<std::byte[]> data_;
    mutable nb::ft_mutex mutex_;
};

} // namespace sharedbox
