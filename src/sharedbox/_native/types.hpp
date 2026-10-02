#pragma once

#include <Python.h>
#include <nanobind/nanobind.h>
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
    /// TypedDict: whether each key must be present.
    std::vector<bool> required;
    /// array: hand the converter uint16 elements, which it reinterprets (numpy has no bfloat16).
    bool as_uint16 = false;
};

/// The field types of one class: the description table, parsed by sharedbox.hpp, each field's label,
/// and what converting values needs from Python. Fixed once made.
/// The field's container index in bytearray keys.
inline constexpr std::uint32_t field_container = detail::no_node;

class Types {
public:
    /// fields as Segment.create takes them; info maps a description's offset to its tuple; bytearrays
    /// lists (description offset, member) of each bytes member read back as bytearray, offset -1 for a
    /// field.
    Types(const std::vector<std::tuple<std::uint32_t, std::uint32_t, std::uint32_t>> &fields,
          std::vector<std::string> labels, std::string table, const nb::dict &info,
          const std::vector<std::pair<std::int64_t, std::uint32_t>> &bytearrays);

    const std::string &table() const { return table_; }
    std::size_t field_count() const { return labels_.size(); }
    const detail::type_ref &field(std::uint32_t index) const;
    const std::string &label(std::uint32_t index) const;

    /// The bytes to store for value in field index, held in buffer or in value; for a list, set or dict
    /// its length and the slots it uses. Raises a Python exception naming the field.
    std::span<const std::byte> encode(std::uint32_t index, PyObject *value, EncodeBuffer &buffer) const;
    /// The value stored in bytes. owned, when given, holds those bytes on the heap, and an array takes it
    /// instead of copying.
    nb::object decode(std::uint32_t index, std::span<const std::byte> bytes,
                      std::unique_ptr<std::byte[]> *owned) const;

    /// Whether field index is a bytes field read back as bytearray.
    bool field_is_bytearray(std::uint32_t index) const { return read_as_bytearray(field_container, index); }

    int traverse(visitproc visit, void *arg) const;
    void clear();

private:
    struct Where {
        const std::string &name;
    };
    void encode_into(const detail::type_ref &t, PyObject *value, std::byte *out, const Where &where) const;
    nb::object decode_from(const detail::type_ref &t, const std::byte *data, std::uint32_t container,
                           std::uint32_t member, const Where &where) const;
    bool accepts(const detail::type_ref &t, PyObject *value) const;
    void prepare(std::uint32_t node, std::uint8_t kind, nb::object info);
    bool read_as_bytearray(std::uint32_t container, std::uint32_t member) const;

    detail::type_table tree_;
    std::vector<std::string> labels_;
    std::string table_;
    std::vector<NodeInfo> nodes_;
    std::vector<std::uint64_t> bytearrays_;
};

} // namespace sharedbox
