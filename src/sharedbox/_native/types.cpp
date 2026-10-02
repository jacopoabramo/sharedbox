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

// Item i of a description's Python tuple, borrowed; invalid_argument if the tuple has another shape.
PyObject *item(const nb::object &info, Py_ssize_t i) {
    if (!PyTuple_Check(info.ptr()) || PyTuple_Size(info.ptr()) <= i)
        throw std::invalid_argument("the Python data of a description has the wrong shape");
    return PyTuple_GetItem(info.ptr(), i);
}

std::string type_name(PyObject *type) {
    const nb::object name = nb::handle(type).attr("__qualname__");
    return nb::borrow<nb::str>(name).c_str();
}

[[noreturn]] void wrong(const std::string &where, const std::string &expected, PyObject *value) {
    raise(PyExc_TypeError,
          where + " expects " + expected + ", got " + type_name(reinterpret_cast<PyObject *>(Py_TYPE(value))));
}

[[noreturn]] void corrupt(const std::string &where) {
    raise(PyExc_ValueError, where + ": the stored value is not one Python can hold");
}

bool instance(PyObject *value, PyObject *type) {
    const int found = PyObject_IsInstance(value, type);
    if (found < 0)
        throw nb::python_error();
    return found == 1;
}

// The position of value among a literal's values, matching type and value, so 1 and True stay apart;
// -1 if it is none of them.
int literal_position(const NodeInfo &n, PyObject *value) {
    for (std::size_t i = 0; i < n.items.size(); ++i) {
        PyObject *candidate = n.items[i];
        if (Py_TYPE(candidate) != Py_TYPE(value))
            continue;
        if (candidate == value)
            return static_cast<int>(i);
        const int equal = PyObject_RichCompareBool(candidate, value, Py_EQ);
        if (equal < 0)
            throw nb::python_error();
        if (equal == 1)
            return static_cast<int>(i);
    }
    return -1;
}

template <class T> T load(const std::byte *data) {
    T value;
    std::memcpy(&value, data, sizeof value);
    return value;
}

template <class T> void put(std::byte *out, T value) { std::memcpy(out, &value, sizeof value); }

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
    const std::uint16_t count = tree_.node(node).count;
    switch (kind) {
    case kind_enum: {
        n.cls = item(n.info, 0);
        PyObject *members = item(n.info, 1);
        if (!PyTuple_Check(members) || PyTuple_Size(members) != count)
            throw std::invalid_argument("an enum's members do not match its description");
        for (std::uint16_t i = 0; i < count; ++i) {
            n.items.push_back(PyTuple_GetItem(members, i));
            n.positions.emplace(n.items.back(), i);
        }
        break;
    }
    case kind_flag:
        n.cls = item(n.info, 0);
        break;
    case kind_literal: {
        PyObject *values = item(n.info, 0);
        if (!PyTuple_Check(values) || PyTuple_Size(values) != count)
            throw std::invalid_argument("a literal's values do not match its description");
        for (std::uint16_t i = 0; i < count; ++i)
            n.items.push_back(PyTuple_GetItem(values, i));
        break;
    }
    case kind_union: {
        PyObject *order = item(n.info, 0);
        PyObject *exact = item(n.info, 1);
        if (!PyTuple_Check(order) || !PyTuple_Check(exact) || PyTuple_Size(order) != count ||
            PyTuple_Size(exact) != count)
            throw std::invalid_argument("a union's order and classes do not match its description");
        for (std::uint16_t i = 0; i < count; ++i) {
            const long tag = PyLong_AsLong(PyTuple_GetItem(order, i));
            if (tag < 0 || tag >= count)
                throw std::invalid_argument("a union's order names a member it does not have");
            n.order.push_back(static_cast<std::uint8_t>(tag));
            n.items.push_back(PyTuple_GetItem(exact, i));
        }
        break;
    }
    case kind_record:
    case kind_tuple: {
        n.form = static_cast<int>(PyLong_AsLong(item(n.info, 0)));
        n.cls = item(n.info, 1);
        PyObject *attrs = item(n.info, 2);
        PyObject *keywords = item(n.info, 3);
        PyObject *required = item(n.info, 4);
        const bool named = n.form != 2;
        if (n.form < 0 || n.form > 3 || !PyTuple_Check(attrs) || !PyTuple_Check(keywords) ||
            !PyTuple_Check(required) ||
            (named && (PyTuple_Size(attrs) != count || PyTuple_Size(keywords) != count)) ||
            (n.form == 3 && PyTuple_Size(required) != count))
            throw std::invalid_argument("a record's Python data does not match its description");
        for (Py_ssize_t i = 0; named && i < count; ++i) {
            n.attrs.push_back(PyTuple_GetItem(attrs, i));
            n.keywords.push_back(PyTuple_GetItem(keywords, i));
        }
        for (Py_ssize_t i = 0; n.form == 3 && i < count; ++i)
            n.required.push_back(PyTuple_GetItem(required, i) == Py_True);
        break;
    }
    default:
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
    if (t.kind <= kind_bytes) {
        EncodeBuffer scratch;
        const std::span<const std::byte> bytes = sharedbox::encode(desc_of(t), where.name, value, scratch);
        if (detail::prefixed(t.kind)) {
            put(out, static_cast<std::uint32_t>(bytes.size()));
            out += 4;
        }
        if (!bytes.empty())
            std::memcpy(out, bytes.data(), bytes.size());
        return;
    }
    if (t.kind >= kind_complex && t.kind <= kind_uuid) {
        scalars::encode(t.kind, value, {out, t.size}, where.name);
        return;
    }
    if (t.kind == kind_decimal) {
        const std::string text = scalars::decimal_text(value, t.size - 4, where.name);
        put(out, static_cast<std::uint32_t>(text.size()));
        if (!text.empty())
            std::memcpy(out + 4, text.data(), text.size());
        return;
    }
    const NodeInfo &n = nodes_[t.node];
    switch (t.kind) {
    case kind_enum: {
        if (reinterpret_cast<PyObject *>(Py_TYPE(value)) != n.cls)
            wrong(where.name, "a " + type_name(n.cls) + " member", value);
        put(out, n.positions.at(value));
        return;
    }
    case kind_flag: {
        if (!instance(value, n.cls))
            wrong(where.name, "a " + type_name(n.cls), value);
        const nb::object number = nb::handle(value).attr("value");
        const unsigned long long bits = PyLong_AsUnsignedLongLong(number.ptr());
        if (bits == static_cast<unsigned long long>(-1) && PyErr_Occurred()) {
            PyErr_Clear();
            raise(PyExc_ValueError, where.name + " holds flag bits of 0 to 2**64 - 1; the value's do not fit");
        }
        put(out, static_cast<std::uint64_t>(bits));
        return;
    }
    case kind_literal: {
        const int position = literal_position(n, value);
        if (position < 0) {
            const nb::object shown = nb::steal(PyObject_Repr(value));
            raise(PyExc_ValueError, where.name + " expects one of its Literal values, got " +
                                        std::string(nb::borrow<nb::str>(shown).c_str()));
        }
        put(out, static_cast<std::uint16_t>(position));
        return;
    }
    case kind_optional: {
        const detail::type_member &inner = tree_.member(tree_.node(t.node).members);
        if (value == Py_None)
            return;
        out[0] = std::byte{1};
        encode_into(inner.type, value, out + inner.offset, where);
        return;
    }
    case kind_union: {
        const detail::type_node &node = tree_.node(t.node);
        int chosen = -1;
        for (const std::uint8_t tag : n.order)
            if (n.items[tag] != Py_None && reinterpret_cast<PyObject *>(Py_TYPE(value)) == n.items[tag]) {
                chosen = tag;
                break;
            }
        for (std::size_t k = 0; chosen < 0 && k < n.order.size(); ++k)
            if (accepts(tree_.member(node.members + n.order[k]).type, value))
                chosen = n.order[k];
        if (chosen < 0)
            wrong(where.name, "a value of one of its union's types", value);
        const detail::type_member &m = tree_.member(node.members + static_cast<std::uint32_t>(chosen));
        out[0] = static_cast<std::byte>(chosen);
        encode_into(m.type, value, out + m.offset, where);
        return;
    }
    case kind_record:
    case kind_tuple: {
        const detail::type_node &node = tree_.node(t.node);
        auto member = [&](std::uint32_t i) -> const detail::type_member & {
            return tree_.member(node.members + i);
        };
        if (n.form == 3) {
            if (!instance(value, scalars::mapping()))
                wrong(where.name, "a mapping with the TypedDict's keys", value);
            const nb::object keys = nb::steal(PyMapping_Keys(value));
            if (!keys.is_valid())
                throw nb::python_error();
            for (nb::handle key : nb::borrow<nb::list>(keys))
                if (std::find_if(n.attrs.begin(), n.attrs.end(), [&](PyObject *a) {
                        return PyUnicode_Check(key.ptr()) && PyUnicode_Compare(a, key.ptr()) == 0;
                    }) == n.attrs.end()) {
                    const nb::object shown = nb::steal(PyObject_Repr(key.ptr()));
                    raise(PyExc_TypeError, where.name + " has no key " + nb::borrow<nb::str>(shown).c_str());
                }
            for (std::uint32_t i = 0; i < node.count; ++i) {
                // Absence is decided from the keys, since GetItem on a defaultdict would insert the key.
                const int has = PySequence_Contains(keys.ptr(), n.attrs[i]);
                if (has < 0)
                    throw nb::python_error();
                nb::object v = has == 1 ? nb::steal(PyObject_GetItem(value, n.attrs[i])) : nb::object();
                if (has == 1 && !v.is_valid())
                    throw nb::python_error();
                if (has == 0) {
                    if (n.required[i])
                        raise(PyExc_TypeError, where.name + " needs key '" +
                                                   std::string(nb::borrow<nb::str>(n.attrs[i]).c_str()) + "'");
                    continue;
                }
                const detail::type_member &m = member(i);
                if (n.required[i]) {
                    encode_into(m.type, v.ptr(), out + m.offset, where);
                } else {
                    // A key that may be missing is an optional whose presence byte says it was there, so a None
                    // value under it stays a None value.
                    const detail::type_member &inner = tree_.member(tree_.node(m.type.node).members);
                    out[m.offset] = std::byte{1};
                    encode_into(inner.type, v.ptr(), out + m.offset + inner.offset, where);
                }
            }
            return;
        }
        if (n.form == 2 ? !PyTuple_Check(value) : !instance(value, n.cls))
            wrong(where.name, n.form == 2 ? std::string("a tuple") : "a " + type_name(n.cls), value);
        if (n.form != 0 && PyTuple_Size(value) != node.count)
            raise(PyExc_ValueError, where.name + " holds a tuple of " + std::to_string(node.count) +
                                        " items; the value has " + std::to_string(PyTuple_Size(value)));
        for (std::uint32_t i = 0; i < node.count; ++i) {
            const nb::object v = n.form == 0 ? nb::steal(PyObject_GetAttr(value, n.attrs[i]))
                                             : nb::borrow(PyTuple_GetItem(value, static_cast<Py_ssize_t>(i)));
            if (!v.is_valid())
                throw nb::python_error();
            encode_into(member(i).type, v.ptr(), out + member(i).offset, where);
        }
        return;
    }
    default:
        raise(PyExc_TypeError,
              where.name + ": values of kind " + std::to_string(t.kind) + " are not supported yet");
    }
}

nb::object Types::decode_from(const detail::type_ref &t, const std::byte *data, std::uint32_t container,
                              std::uint32_t member, const Where &where) const {
    if (t.kind <= kind_bytes) {
        std::size_t size = t.size;
        if (detail::prefixed(t.kind)) {
            const auto length = load<std::uint32_t>(data);
            if (length > t.size - 4)
                corrupt(where.name);
            data += 4;
            size = length;
        } else if (t.kind == kind_bool && load<std::uint8_t>(data) > 1) {
            corrupt(where.name);
        }
        if (t.kind == kind_bytes && read_as_bytearray(container, member))
            return nb::steal(PyByteArray_FromStringAndSize(reinterpret_cast<const char *>(data),
                                                           static_cast<Py_ssize_t>(size)));
        PyObject *value = sharedbox::decode(desc_of(t), reinterpret_cast<const char *>(data), size);
        if (value == nullptr)
            throw nb::python_error();
        return nb::steal(value);
    }
    if (t.kind >= kind_complex && t.kind <= kind_uuid) {
        PyObject *value = scalars::decode(t.kind, {data, t.size}, where.name);
        if (value == nullptr)
            throw nb::python_error();
        return nb::steal(value);
    }
    if (t.kind == kind_decimal) {
        const auto length = load<std::uint32_t>(data);
        if (length > t.size - 4)
            corrupt(where.name);
        PyObject *value = scalars::decode(kind_decimal, {data + 4, length}, where.name);
        if (value == nullptr)
            throw nb::python_error();
        return nb::steal(value);
    }
    const NodeInfo &n = nodes_[t.node];
    const detail::type_node &node = tree_.node(t.node);
    switch (t.kind) {
    case kind_enum:
    case kind_literal: {
        const auto position = load<std::uint16_t>(data);
        if (position >= node.count)
            corrupt(where.name);
        return nb::borrow(n.items[position]);
    }
    case kind_flag: {
        const nb::object bits = nb::steal(PyLong_FromUnsignedLongLong(load<std::uint64_t>(data)));
        PyObject *made = PyObject_CallFunctionObjArgs(n.cls, bits.ptr(), nullptr);
        if (made == nullptr)
            throw nb::python_error();
        return nb::steal(made);
    }
    case kind_optional: {
        const auto present = load<std::uint8_t>(data);
        if (present > 1)
            corrupt(where.name);
        if (present == 0)
            return nb::none();
        const detail::type_member &inner = tree_.member(node.members);
        return decode_from(inner.type, data + inner.offset, t.node, 0, where);
    }
    case kind_union: {
        const auto tag = load<std::uint8_t>(data);
        if (tag >= node.count)
            corrupt(where.name);
        const detail::type_member &m = tree_.member(node.members + tag);
        return decode_from(m.type, data + m.offset, t.node, tag, where);
    }
    case kind_record:
    case kind_tuple: {
        auto member = [&](std::uint32_t i) -> const detail::type_member & {
            return tree_.member(node.members + i);
        };
        if (n.form == 3) {
            nb::dict out;
            for (std::uint32_t i = 0; i < node.count; ++i) {
                const detail::type_member &m = member(i);
                if (n.required[i]) {
                    out[nb::handle(n.attrs[i])] = decode_from(m.type, data + m.offset, t.node, i, where);
                    continue;
                }
                const auto present = load<std::uint8_t>(data + m.offset);
                if (present > 1)
                    corrupt(where.name);
                if (present == 1) {
                    const detail::type_member &inner = tree_.member(tree_.node(m.type.node).members);
                    out[nb::handle(n.attrs[i])] =
                        decode_from(inner.type, data + m.offset + inner.offset, m.type.node, 0, where);
                }
            }
            return out;
        }
        if (n.form == 0) {
            nb::dict kwargs;
            for (std::uint32_t i = 0; i < node.count; ++i)
                kwargs[nb::handle(n.keywords[i])] =
                    decode_from(member(i).type, data + member(i).offset, t.node, i, where);
            PyObject *made = PyObject_Call(n.cls, nb::tuple().ptr(), kwargs.ptr());
            if (made == nullptr)
                throw nb::python_error();
            return nb::steal(made);
        }
        nb::object items = nb::steal(PyTuple_New(node.count));
        if (!items.is_valid())
            throw nb::python_error();
        for (std::uint32_t i = 0; i < node.count; ++i) {
            nb::object v = decode_from(member(i).type, data + member(i).offset, t.node, i, where);
            PyTuple_SetItem(items.ptr(), static_cast<Py_ssize_t>(i), v.release().ptr());
        }
        if (n.form == 2)
            return items;
        PyObject *made = PyObject_CallObject(n.cls, items.ptr());
        if (made == nullptr)
            throw nb::python_error();
        return nb::steal(made);
    }
    default:
        raise(PyExc_TypeError,
              where.name + ": values of kind " + std::to_string(t.kind) + " are not supported yet");
    }
}

bool Types::accepts(const detail::type_ref &t, PyObject *value) const {
    switch (t.kind) {
    case kind_bool:
        return PyBool_Check(value);
    case kind_int:
        return PyLong_Check(value) && !PyBool_Check(value);
    case kind_float:
        return (PyFloat_Check(value) || PyLong_Check(value)) && !PyBool_Check(value);
    case kind_str:
        return PyUnicode_Check(value);
    case kind_bytes:
        return PyBytes_Check(value) || PyByteArray_Check(value) || PyMemoryView_Check(value);
    default:
        break;
    }
    if (t.kind >= kind_complex && t.kind <= kind_decimal)
        return scalars::accepts(t.kind, value);
    const NodeInfo &n = nodes_[t.node];
    switch (t.kind) {
    case kind_enum:
        return reinterpret_cast<PyObject *>(Py_TYPE(value)) == n.cls;
    case kind_flag:
        return instance(value, n.cls);
    case kind_literal:
        return literal_position(n, value) >= 0;
    case kind_optional:
        return value == Py_None || accepts(tree_.member(tree_.node(t.node).members).type, value);
    case kind_union: {
        const detail::type_node &node = tree_.node(t.node);
        for (std::uint32_t i = 0; i < node.count; ++i)
            if (accepts(tree_.member(node.members + i).type, value))
                return true;
        return false;
    }
    case kind_record:
    case kind_tuple:
        if (n.form == 3)
            return instance(value, scalars::mapping());
        return n.form == 2 ? PyTuple_Check(value) != 0 : instance(value, n.cls);
    default:
        return false;
    }
}

} // namespace sharedbox
