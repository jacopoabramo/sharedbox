#pragma once

#include <Python.h>
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <sharedbox/sharedbox.hpp>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

#include "codec.hpp"

namespace sharedbox {

namespace nb = nanobind;

/// What converting values of one described type needs from Python, taken once from the tuple the
/// layout built for it. Every pointer is borrowed from info, which keeps it alive.
struct NodeInfo {
    nb::object info;
    /// enum: the members; literal: the values; union: each member's exact class, or None.
    std::vector<PyObject *> items;
    /// enum: each member's position, found by identity, so no Python __hash__ runs.
    std::unordered_map<PyObject *, std::uint16_t> positions;
    /// union: member indices in the order they are tried.
    std::vector<std::uint8_t> order;
    /// enum, flag, record: the class; list, set, dict: the type read back; array: the converter or None.
    PyObject *cls = nullptr;
    /// record: 0 a class built by keyword, 1 a class built from a tuple, 2 a plain tuple, 3 a TypedDict.
    int form = 0;
    /// record: the name each member is read by (attribute or key), and the keyword it is passed as.
    std::vector<PyObject *> attrs;
    std::vector<PyObject *> keywords;
    /// record built by keyword: its class takes every member by position, in member order.
    bool by_position = false;
    /// TypedDict: whether each key must be present.
    std::vector<bool> required;
    /// array: hand the converter uint16 elements, which it reinterprets (numpy has no bfloat16).
    bool as_uint16 = false;
};

/// One entry of the converts list: description offset (-1 for a field), member, read_as, write_as.
using Convert = std::tuple<std::int64_t, std::uint32_t, nb::object, nb::object>;

/// The field's container index in bytearray keys.
inline constexpr std::uint32_t field_container = detail::no_node;

/// The field types of one class: the description table, parsed by sharedbox.hpp, each field's label,
/// and what converting values needs from Python. Fixed once made.
class Types {
public:
    /// fields as Segment.create takes them; info maps a description's offset to its tuple; bytearrays
    /// lists (description offset, member) of each bytes member read back as bytearray, offset -1 for a
    /// field; reusable lists the fields whose values cannot be changed once read; converts lists the
    /// callables a member's values pass through when read and before they are written, either may be None.
    Types(const std::vector<std::tuple<std::uint32_t, std::uint32_t, std::uint32_t>> &fields,
          std::vector<std::string> labels, std::string table, const nb::dict &info,
          const std::vector<std::pair<std::int64_t, std::uint32_t>> &bytearrays,
          const std::vector<std::uint32_t> &reusable = {}, const std::vector<Convert> &converts = {});

    const std::string &table() const { return table_; }
    std::size_t field_count() const { return labels_.size(); }
    const detail::type_ref &field(std::uint32_t index) const;
    const std::string &label(std::uint32_t index) const;

    /// The bytes to store for value in field index, held in buffer or in value; for a list, set or dict
    /// its length and the slots it uses. Raises a Python exception naming the field.
    std::span<const std::byte> encode(std::uint32_t index, PyObject *value, EncodeBuffer &buffer) const;
    /// Writes value into out, the size of field index, which may still hold an older value. An array already
    /// at its place in out is left as it is, and an array that views out elsewhere is stored with the values
    /// it held when the call began. Raises a Python exception naming the field, possibly after writing part of
    /// out.
    void encode_to(std::uint32_t index, PyObject *value, std::span<std::byte> out) const;
    /// The value stored in bytes. owned, when given, holds those bytes on the heap, and an array takes it
    /// instead of copying.
    nb::object decode(std::uint32_t index, std::span<const std::byte> bytes,
                      std::unique_ptr<std::byte[]> *owned) const;

    /// Whether field index is a bytes field read back as bytearray.
    bool field_is_bytearray(std::uint32_t index) const { return read_as_bytearray(field_container, index); }
    /// Whether a read of field index, of kind 0 to 5, must go through decode: a bytearray or a conversion.
    bool field_via_types(std::uint32_t index) const noexcept {
        return index < via_types_.size() && via_types_[index];
    }

    /// Whether a read of field index may return the value an earlier read decoded.
    bool field_reusable(std::uint32_t index) const { return index < reusable_.size() && reusable_[index]; }

    /// out as a writable destination for array field index. Raises TypeError unless the field is an array and
    /// out a writable C-contiguous array in CPU memory, and ValueError unless out has the field's dtype and
    /// shape; an ml_dtypes bfloat16 array is taken as its uint16 view.
    nb::ndarray<nb::c_contig, nb::device::cpu> array_out(std::uint32_t index, PyObject *out) const;
    /// Where an array the caller passed to receive into goes: its writable view, where its bytes start in the
    /// field's value and how many there are, and the key decode_given finds it under.
    struct ArrayTarget {
        nb::ndarray<nb::c_contig, nb::device::cpu> view;
        std::uint64_t offset = 0;
        std::uint64_t size = 0;
        std::uint64_t key = 0;
    };

    /// out as the destination of the array that path reaches in field index, through records and tuples; the
    /// empty path is the field itself. Raises as array_out does, and TypeError for a path that steps into
    /// anything else or does not end at an array.
    ArrayTarget array_target(std::uint32_t index, std::span<const std::uint32_t> path, PyObject *out) const;
    /// As decode for field index, with the member under each key of given taken as that object.
    nb::object decode_given(std::uint32_t index, std::span<const std::byte> bytes,
                            const std::unordered_map<std::uint64_t, PyObject *> &given) const;
    /// A writable array over data, the bytes of array field index, which owner keeps alive, passed through the
    /// field's converter as a read is. Raises TypeError unless the field is an array.
    nb::object array_over(std::uint32_t index, std::byte *data, nb::handle owner) const;
    /// The value of field index built from template_item, with each array that records and tuples lead to
    /// replaced by a writable array over its place in data, which owner keeps alive. Members that lead to no
    /// array are template_item's own objects, and records are built as decode builds them. A template_item
    /// of None is taken only for an array field. Raises TypeError naming the field for a template_item of
    /// another type, and ValueError for one that leads to no array and holds a value that does not fit.
    nb::object with_arrays(std::uint32_t index, PyObject *template_item, std::byte *data, nb::handle owner) const;

    int traverse(visitproc visit, void *arg) const;
    void clear();

private:
    /// An array encode_to copies to out once every other byte of the value is written.
    struct PendingArray {
        nb::ndarray<nb::ro> view;
        std::byte *out = nullptr;
    };
    struct Where {
        const std::string &name;
        const std::unordered_map<std::uint64_t, PyObject *> *given = nullptr;
        /// When set, an array is added here instead of copied at once.
        std::vector<PendingArray> *pending = nullptr;
    };
    nb::ndarray<nb::c_contig, nb::device::cpu> array_out_of(const detail::type_ref &t, const std::string &name,
                                                            PyObject *out) const;
    void encode_into(const detail::type_ref &t, PyObject *value, std::byte *out, const Where &where) const;
    /// Whether an array can be reached from t through records and tuples.
    bool holds_array(const detail::type_ref &t) const;
    nb::object rebuild(const detail::type_ref &t, PyObject *value, std::byte *data, nb::handle owner,
                       const Where &where) const;
    /// decode_stored, then t's read conversion.
    nb::object decode_from(const detail::type_ref &t, const std::byte *data, std::uint32_t container,
                           std::uint32_t member, const Where &where) const;
    nb::object decode_stored(const detail::type_ref &t, const std::byte *data, std::uint32_t container,
                             std::uint32_t member, const Where &where) const;
    /// Writes a list, set or dict value at out: its length, then its slots; returns the bytes used.
    std::size_t encode_collection(const detail::type_ref &t, PyObject *value, std::byte *out,
                                  const Where &where) const;
    bool accepts(const detail::type_ref &t, PyObject *value) const;
    /// The array view of value, checked against an array type; raises TypeError or ValueError naming the
    /// field. The view keeps the memory it points into exported and alive.
    nb::ndarray<nb::ro> array_view(const detail::type_ref &t, PyObject *value, const Where &where) const;
    /// Copies an array view into out, gathering a strided one in C order. A copy of
    /// detail::large_copy bytes or more runs with the GIL released.
    static void copy_array(const nb::ndarray<nb::ro> &view, std::byte *out);
    /// An array value whose bytes start at data; owned, when it holds data, is taken instead of copied.
    nb::object decode_array(const detail::type_ref &t, const std::byte *data,
                            std::unique_ptr<std::byte[]> *owned) const;
    /// The array over raw that owner keeps alive, with the node's shape and dtype, through the converter.
    nb::object wrap_array(const detail::type_ref &t, std::byte *raw, nb::handle owner) const;
    void prepare(std::uint32_t node, std::uint8_t kind, nb::object info);
    bool read_as_bytearray(std::uint32_t container, std::uint32_t member) const;

    detail::type_table tree_;
    std::vector<std::string> labels_;
    std::string table_;
    std::vector<NodeInfo> nodes_;
    std::vector<std::uint64_t> bytearrays_;
    std::vector<bool> reusable_;
    std::vector<bool> via_types_;
    /// The read and write conversions of a field or member, found by its type_ref in tree_.
    struct Conversion {
        nb::object read_as;
        nb::object write_as;
    };
    std::unordered_map<const detail::type_ref *, Conversion> conversions_;
    /// The conversion of t, or nullptr; one test while there are none.
    const Conversion *conversion(const detail::type_ref &t) const {
        if (conversions_.empty()) [[likely]]
            return nullptr;
        const auto found = conversions_.find(&t);
        return found == conversions_.end() ? nullptr : &found->second;
    }
    /// value passed through f, or value itself when f is None.
    static nb::object through(const nb::object &f, nb::object value);
    /// encode_into without the write conversion.
    void encode_plain(const detail::type_ref &t, PyObject *value, std::byte *out, const Where &where) const;
    /// encode_plain of value passed through c.write_as, with the field's name in front of its error.
    SBX_NOINLINE void encode_converted(const Conversion &c, const detail::type_ref &t, PyObject *value,
                                       std::byte *out, const Where &where) const;
};

} // namespace sharedbox
