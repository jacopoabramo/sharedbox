#include "types.hpp"

#include <algorithm>
#include <cstring>
#include <stdexcept>

#include "scalars.hpp"

namespace sharedbox {
namespace {

[[noreturn]] void raise(PyObject *type, const std::string &message) {
    PyErr_SetString(type, message.c_str());
    throw nb::python_error();
}

std::uint64_t bytearray_key(std::uint32_t container, std::uint32_t member) {
    return (std::uint64_t{container} << 32) | member;
}

FieldDesc desc_of(const detail::type_ref &t) {
    return {0, detail::prefixed(t.kind) ? t.size - 4 : t.size, static_cast<FieldKind>(t.kind)};
}

} // namespace

const Types &need(const Types *types, std::uint32_t index) {
    if (types == nullptr)
        raise(PyExc_TypeError,
              "field " + std::to_string(index) + " has a kind above 5, which needs the class's Types to convert");
    return *types;
}

Types::Types(const std::vector<std::tuple<std::uint32_t, std::uint32_t, std::uint32_t>> &fields,
             std::vector<std::string> labels, std::string table, const nb::dict &info,
             const std::vector<std::pair<std::int64_t, std::uint32_t>> &bytearrays)
    : labels_(std::move(labels)), table_(std::move(table)) {
    check_names(labels_, fields.size());
    std::vector<std::uint32_t> entries;
    entries.reserve(fields.size());
    for (const auto &[offset, capacity, kind] : fields) {
        if (capacity > capacity_mask || kind > 255)
            throw std::invalid_argument("a field's capacity or kind is out of range");
        entries.push_back(capacity | kind << kind_shift);
    }
    const auto *bytes = reinterpret_cast<const std::byte *>(table_.data());
    if (tree_.parse({bytes, table_.size()}, entries, false) != status::ok)
        throw std::invalid_argument("the description table does not pass the layout's checks");
    for (std::size_t i = 0; i < fields.size(); ++i)
        if (!detail::kind_known(tree_.field(static_cast<std::uint16_t>(i)).kind))
            throw std::invalid_argument("field " + std::to_string(i) + " has a kind this version does not know");
    nodes_.resize(tree_.node_count());
    for (std::uint32_t n = 0; n < tree_.node_count(); ++n) {
        const detail::type_node &node = tree_.node(n);
        const nb::object at = nb::int_(node.at);
        nb::object item = nb::none();
        if (info.contains(at))
            item = info[at];
        std::uint8_t kind = 0;
        std::memcpy(&kind, bytes + node.at, 1);
        prepare(n, kind, std::move(item));
    }
    for (const auto &[offset, member] : bytearrays) {
        std::uint32_t container = field_container;
        if (offset >= 0) {
            container = UINT32_MAX - 1;
            for (std::uint32_t n = 0; n < tree_.node_count(); ++n)
                if (tree_.node(n).at == static_cast<std::uint64_t>(offset))
                    container = n;
            if (container == UINT32_MAX - 1)
                throw std::invalid_argument("bytearrays names no description at offset " + std::to_string(offset));
        }
        bytearrays_.push_back(bytearray_key(container, member));
    }
    std::sort(bytearrays_.begin(), bytearrays_.end());
}

const detail::type_ref &Types::field(std::uint32_t index) const {
    check_index(index, labels_.size());
    return tree_.field(static_cast<std::uint16_t>(index));
}

const std::string &Types::label(std::uint32_t index) const {
    check_index(index, labels_.size());
    return labels_[index];
}

bool Types::read_as_bytearray(std::uint32_t container, std::uint32_t member) const {
    return std::binary_search(bytearrays_.begin(), bytearrays_.end(), bytearray_key(container, member));
}

void Types::prepare(std::uint32_t node, std::uint8_t kind, nb::object info) {
    NodeInfo &n = nodes_[node];
    n.info = std::move(info);
    switch (kind) {
    default:
        // A kind with no Python data of its own keeps only the tuple.
        break;
    }
}

int Types::traverse(visitproc visit, void *arg) const {
    for (const NodeInfo &n : nodes_)
        Py_VISIT(n.info.ptr());
    return 0;
}

void Types::clear() {
    for (NodeInfo &n : nodes_)
        n = NodeInfo{};
}

std::span<const std::byte> Types::encode(std::uint32_t index, PyObject *value, EncodeBuffer &buffer) const {
    const detail::type_ref &t = field(index);
    const std::string &name = labels_[index];
    if (t.kind <= kind_ref)
        return sharedbox::encode(desc_of(t), name, value, buffer);
    if (t.kind == kind_decimal) {
        buffer.text = scalars::decimal_text(value, t.size - 4, name);
        return std::as_bytes(std::span(buffer.text.data(), buffer.text.size()));
    }
    std::byte *out = buffer.reserve(t.size);
    encode_into(t, value, out, {name});
    return {out, t.size};
}

nb::object Types::decode(std::uint32_t index, std::span<const std::byte> bytes,
                         std::unique_ptr<std::byte[]> *owned) const {
    static_cast<void>(owned);
    const detail::type_ref &t = field(index);
    const std::string &name = labels_[index];
    // A list, set or dict is read as its length and the slots it uses, so it may be shorter than its size.
    const bool fits = detail::prefixed(t.kind)        ? bytes.size() <= t.size - 4
                      : detail::is_collection(t.kind) ? bytes.size() <= t.size
                                                      : bytes.size() == t.size;
    if (!fits)
        raise(PyExc_ValueError, name + ": " + std::to_string(bytes.size()) +
                                    " bytes do not fit a value that takes " + std::to_string(t.size));
    if (t.kind <= kind_ref) {
        if (t.kind == kind_bytes && field_is_bytearray(index))
            return nb::steal(PyByteArray_FromStringAndSize(reinterpret_cast<const char *>(bytes.data()),
                                                           static_cast<Py_ssize_t>(bytes.size())));
        PyObject *value =
            sharedbox::decode(desc_of(t), reinterpret_cast<const char *>(bytes.data()), bytes.size());
        if (value == nullptr)
            throw nb::python_error();
        return nb::steal(value);
    }
    if (t.kind == kind_decimal) {
        PyObject *value = scalars::decode(kind_decimal, bytes, name);
        if (value == nullptr)
            throw nb::python_error();
        return nb::steal(value);
    }
    return decode_from(t, bytes.data(), field_container, index, {name});
}

void Types::encode_into(const detail::type_ref &t, PyObject *value, std::byte *out, const Where &where) const {
    if (t.kind >= kind_complex && t.kind <= kind_uuid) {
        scalars::encode(t.kind, value, {out, t.size}, where.name);
        return;
    }
    raise(PyExc_TypeError, where.name + ": values of kind " + std::to_string(t.kind) + " are not supported yet");
}

nb::object Types::decode_from(const detail::type_ref &t, const std::byte *data, std::uint32_t container,
                              std::uint32_t member, const Where &where) const {
    static_cast<void>(container);
    static_cast<void>(member);
    if (t.kind >= kind_complex && t.kind <= kind_uuid) {
        PyObject *value = scalars::decode(t.kind, {data, t.size}, where.name);
        if (value == nullptr)
            throw nb::python_error();
        return nb::steal(value);
    }
    raise(PyExc_TypeError, where.name + ": values of kind " + std::to_string(t.kind) + " are not supported yet");
}

bool Types::accepts(const detail::type_ref &t, PyObject *value) const {
    if (t.kind >= kind_complex && t.kind <= kind_decimal)
        return scalars::accepts(t.kind, value);
    return false;
}

} // namespace sharedbox
