#include "types.hpp"

#include <algorithm>
#include <array>
#include <cstring>
#include <stdexcept>

#include "collection.hpp"
#include "scalars.hpp"
#include "set.hpp"

namespace sharedbox {
namespace {

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

enum class family { none, boolean, integer, real, complex, time };

// The family of number a union value belongs to. A subclass of int, such as an IntEnum, has none, so a
// union picks its member by the accepts order as 0.6 did.
family value_family(PyObject *value) {
    if (PyBool_Check(value) || scalars::is_numpy_bool(value))
        return family::boolean;
    if (PyLong_Check(value) && !PyLong_CheckExact(value))
        return family::none;
    if (scalars::is_integer(value))
        return family::integer;
    if (scalars::is_real(value))
        return family::real;
    if (scalars::is_complex(value))
        return family::complex;
    if (scalars::accepts(kind_datetime64, value))
        return family::time;
    return family::none;
}

family kind_family(std::uint32_t kind) {
    switch (kind) {
    case kind_bool:
        return family::boolean;
    case kind_int:
    case kind_int8:
    case kind_int16:
    case kind_int32:
    case kind_uint8:
    case kind_uint16:
    case kind_uint32:
    case kind_uint64:
        return family::integer;
    case kind_float:
    case kind_float16:
    case kind_float32:
        return family::real;
    case kind_complex:
    case kind_complex64:
        return family::complex;
    case kind_datetime64:
        return family::time;
    default:
        return family::none;
    }
}

// A fixed-size kind that scalars encodes and decodes.
bool is_scalar(std::uint32_t kind) {
    return kind >= kind_complex && kind <= kind_datetime64 && kind != kind_decimal;
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

// Stores item, a new reference the container takes, at index of a list or tuple made a moment ago. The
// macros skip the checks a new container has no use for; the Limited API has only the functions.
void store_new_item(PyObject *container, bool is_list, std::uint32_t index, PyObject *item) noexcept {
    const auto i = static_cast<Py_ssize_t>(index);
#ifndef Py_LIMITED_API
    if (is_list)
        PyList_SET_ITEM(container, i, item);
    else
        PyTuple_SET_ITEM(container, i, item);
#else
    if (is_list)
        PyList_SetItem(container, i, item);
    else
        PyTuple_SetItem(container, i, item);
#endif
}

FieldDesc desc_of(const detail::type_ref &t) {
    return {0, detail::prefixed(t.kind) ? t.size - 4 : t.size, static_cast<FieldKind>(t.kind)};
}

// A bool, int, float or str stored at data, as a new reference, or nullptr with a Python error set.
PyObject *decode_plain(const detail::type_ref &t, const std::byte *data, const std::string &name) {
    switch (t.kind) {
    case kind_bool: {
        const auto bit = load<std::uint8_t>(data);
        if (bit > 1)
            corrupt(name);
        return Py_NewRef(bit ? Py_True : Py_False);
    }
    case kind_int:
        return PyLong_FromLongLong(load<long long>(data));
    case kind_float:
        return PyFloat_FromDouble(load<double>(data));
    default: {
        const auto length = load<std::uint32_t>(data);
        if (length > t.size - 4)
            corrupt(name);
        return PyUnicode_DecodeUTF8(reinterpret_cast<const char *>(data + 4), length, "replace");
    }
    }
}

// The elements of a list, set or dict value, taken once: another thread changing the value while it is
// encoded, or while the write waits for the lock on the free-threaded build, cannot change what is stored.
nb::object snapshot(PyObject *value, std::uint8_t kind) {
    PyObject *items = kind == kind_dict ? PyMapping_Items(value) : PySequence_Tuple(value);
    if (items == nullptr)
        throw nb::python_error();
    return nb::steal(items);
}

Py_ssize_t length_of(PyObject *items) { return PyList_Check(items) ? PyList_Size(items) : PyTuple_Size(items); }

// Whether value is a numpy array of ml_dtypes' bfloat16, which has a uint16 view but no DLPack export.
bool numpy_bfloat16(PyObject *value) {
    const nb::object dtype = nb::getattr(value, "dtype", nb::none());
    const nb::object name = nb::getattr(dtype, "name", nb::none());
    return PyUnicode_Check(name.ptr()) && PyUnicode_CompareWithASCIIString(name.ptr(), "bfloat16") == 0;
}

std::string dtype_text(const nb::dlpack::dtype &dtype) {
    static constexpr const char *codes[] = {"int", "uint", "float", "opaque", "bfloat", "complex"};
    if (dtype.code == 6)
        return "bool";
    const std::string base = dtype.code < 6 ? codes[dtype.code] : "code " + std::to_string(dtype.code) + " ";
    return base + std::to_string(dtype.bits);
}

template <class Dim> std::string shape_text(std::size_t ndim, Dim dim) {
    std::string text = "(";
    for (std::size_t i = 0; i < ndim; ++i)
        text += (i == 0 ? "" : ", ") + std::to_string(dim(i));
    return text + (ndim == 1 ? ",)" : ")");
}

// Whether the array's elements lie in C order with no gaps, so one memcpy copies it.
bool contiguous(const nb::ndarray<nb::ro> &view) {
    std::int64_t expected = 1;
    for (std::size_t i = view.ndim(); i-- > 0;) {
        if (view.shape(i) != 1 && view.stride(i) != expected)
            return false;
        expected *= static_cast<std::int64_t>(view.shape(i));
    }
    return true;
}

// Whether the elements view reads lie in [out, out + size), in any order.
bool overlaps(const nb::ndarray<nb::ro> &view, const std::byte *out, std::size_t size) {
    std::int64_t low = 0;
    std::int64_t high = 0;
    for (std::size_t i = 0; i < view.ndim(); ++i) {
        if (view.shape(i) == 0)
            return false;
        const std::int64_t reach = (static_cast<std::int64_t>(view.shape(i)) - 1) * view.stride(i);
        (reach < 0 ? low : high) += reach;
    }
    const auto item = static_cast<std::int64_t>(view.itemsize());
    const auto base = reinterpret_cast<std::intptr_t>(view.data());
    const auto start = reinterpret_cast<std::intptr_t>(out);
    return base + low * item < start + static_cast<std::intptr_t>(size) && start < base + (high + 1) * item;
}

// Raises the TypeError for an out that cannot be filled, naming why: it is not an array, its memory is not
// the CPU's, it has gaps, or it is read-only.
[[noreturn]] void refuse_out(const std::string &name, nb::handle out) {
    nb::ndarray<nb::ro> any;
    if (!nb::try_cast(out, any, false))
        wrong(name, "an array with __dlpack__ or the buffer protocol as out", out.ptr());
    if (any.device_type() != nb::device::cpu::value)
        raise(PyExc_TypeError, name + ": out is not in CPU memory");
    if (!contiguous(any))
        raise(PyExc_TypeError, name + ": out is not C-contiguous");
    raise(PyExc_TypeError, name + ": out is read-only");
}

// Copies an array view into out, gathering a strided one in C order.
void gather(const nb::ndarray<nb::ro> &view, std::byte *out) {
    const std::size_t item = view.itemsize();
    const auto *base = static_cast<const std::byte *>(view.data());
    if (contiguous(view)) {
        std::memcpy(out, base, view.nbytes());
        return;
    }
    std::size_t index[8] = {};
    const std::size_t total = view.size();
    for (std::size_t k = 0; k < total; ++k) {
        std::int64_t at = 0;
        for (std::size_t i = 0; i < view.ndim(); ++i)
            at += static_cast<std::int64_t>(index[i]) * view.stride(i);
        std::memcpy(out + k * item, base + at * static_cast<std::int64_t>(item), item);
        for (std::size_t i = view.ndim(); i-- > 0;) {
            if (++index[i] < view.shape(i))
                break;
            index[i] = 0;
        }
    }
}

PyObject *at(PyObject *items, Py_ssize_t i) {
    return PyList_Check(items) ? PyList_GetItem(items, i) : PyTuple_GetItem(items, i);
}

} // namespace

void corrupt(const std::string &where) {
    raise(PyExc_ValueError, where + ": the stored value is not one Python can hold");
}

const Types &need(const Types *types, std::uint32_t index) {
    if (types == nullptr)
        raise(PyExc_TypeError,
              "field " + std::to_string(index) + " has a kind above 5, which needs the class's Types to convert");
    return *types;
}

Types::Types(const std::vector<std::tuple<std::uint32_t, std::uint32_t, std::uint32_t>> &fields,
             std::vector<std::string> labels, std::string table, const nb::dict &info,
             const std::vector<std::pair<std::int64_t, std::uint32_t>> &bytearrays,
             const std::vector<std::uint32_t> &reusable, const std::vector<Convert> &converts)
    : labels_(std::move(labels)), table_(std::move(table)),
      collection_type_(reinterpret_cast<PyTypeObject *>(nb::type<NativeCollection>().ptr())),
      set_type_(reinterpret_cast<PyTypeObject *>(nb::type<NativeSet>().ptr())) {
    check_names(labels_, fields.size());
    std::vector<std::uint32_t> entries;
    entries.reserve(fields.size());
    for (const auto &[offset, capacity, kind] : fields) {
        if (capacity > capacity_mask || kind > 255)
            throw std::invalid_argument("a field's capacity or kind is out of range");
        entries.push_back(capacity | kind << kind_shift);
    }
    const auto *bytes = reinterpret_cast<const std::byte *>(table_.data());
    if (tree_.parse({bytes, table_.size()}, entries) != status::ok)
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
    const auto node_at = [&](std::int64_t offset, const char *list) {
        for (std::uint32_t n = 0; n < tree_.node_count(); ++n)
            if (tree_.node(n).at == static_cast<std::uint64_t>(offset))
                return n;
        throw std::invalid_argument(std::string(list) + " names no description at offset " +
                                    std::to_string(offset));
    };
    via_types_.resize(fields.size());
    for (const auto &[offset, member] : bytearrays) {
        const std::uint32_t container = offset >= 0 ? node_at(offset, "bytearrays") : field_container;
        if (offset < 0) {
            check_index(member, fields.size());
            via_types_[member] = true;
        }
        bytearrays_.push_back(bytearray_key(container, member));
    }
    std::sort(bytearrays_.begin(), bytearrays_.end());
    for (const auto &[offset, member, read_as, write_as] : converts) {
        const detail::type_ref *t = nullptr;
        if (offset < 0) {
            check_index(member, fields.size());
            via_types_[member] = true;
            t = &tree_.field(static_cast<std::uint16_t>(member));
        } else {
            const detail::type_node &node = tree_.node(node_at(offset, "converts"));
            if (member >= node.count)
                throw std::invalid_argument("converts names a member past its description's count");
            t = &tree_.member(node.members + member).type;
        }
        // Only a fixed-size kind above ref is always encoded here, so only it can take a write conversion.
        if (!write_as.is_none() && (t->kind <= kind_ref || detail::fixed_capacity(t->kind) == 0))
            throw std::invalid_argument("a write conversion needs a fixed-size kind above ref");
        conversions_[t] = {read_as, write_as};
    }
    reusable_.resize(fields.size());
    for (const std::uint32_t index : reusable) {
        check_index(index, fields.size());
        reusable_[index] = true;
    }
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
            if (tag == -1)
                PyErr_Clear();
            if (tag < 0 || tag >= count)
                throw std::invalid_argument("a union's order names a member it does not have");
            n.order.push_back(static_cast<std::uint8_t>(tag));
            n.items.push_back(PyTuple_GetItem(exact, i));
        }
        break;
    }
    case kind_list:
    case kind_set: {
        n.cls = item(n.info, 0);
        PyObject *text = item(n.info, 1);
        if (!PyUnicode_Check(text))
            throw std::invalid_argument("a list's or set's element text is not a str");
        n.element_text = nb::borrow<nb::str>(text).c_str();
        const nb::handle base = kind == kind_list ? nb::type<NativeCollection>() : nb::type<NativeSet>();
        const int stored = PyObject_IsSubclass(n.cls, base.ptr());
        if (stored < 0)
            throw nb::python_error();
        n.stored = stored == 1;
        if (n.stored && kind == kind_set)
            n.key = plan_of(tree_, tree_.member(tree_.node(node).members).type);
        break;
    }
    case kind_dict:
        n.cls = item(n.info, 0);
        break;
    case kind_array:
        n.cls = item(n.info, 0);
        n.as_uint16 = item(n.info, 1) == Py_True;
        break;
    case kind_record:
    case kind_tuple: {
        n.form = static_cast<int>(PyLong_AsLong(item(n.info, 0)));
        if (n.form == -1)
            PyErr_Clear();
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
        if (n.form == 0)
            n.by_position = item(n.info, 5) == Py_True;
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
    for (const auto &[t, c] : conversions_) {
        Py_VISIT(c.read_as.ptr());
        Py_VISIT(c.write_as.ptr());
    }
    return 0;
}

void Types::clear() {
    // Swapped out and dropped on return, so code a decref runs finds this Types already cleared.
    std::vector<NodeInfo> nodes(nodes_.size());
    nodes_.swap(nodes);
    std::unordered_map<const detail::type_ref *, Conversion> conversions;
    conversions_.swap(conversions);
}

nb::object Types::through(const nb::object &f, nb::object value) {
    if (f.is_none())
        return value;
    PyObject *args[1] = {value.ptr()};
    PyObject *made = PyObject_Vectorcall(f.ptr(), args, 1, nullptr);
    return nb::steal(owned(made));
}

std::span<const std::byte> Types::encode(std::uint32_t index, PyObject *value, EncodeBuffer &buffer) const {
    const detail::type_ref &t = field(index);
    const std::string &name = labels_[index];
    if (const Conversion *c = conversion(t); c != nullptr && !c->write_as.is_none()) {
        std::byte *out = buffer.reserve(t.size);
        encode_into(t, value, out, {name});
        return {out, t.size};
    }
    if (t.kind <= kind_ref)
        return sharedbox::encode(desc_of(t), name, value, buffer);
    if (t.kind == kind_decimal) {
        const std::string text = scalars::decimal_text(value, t.size - 4, name);
        std::byte *out = buffer.reserve(text.size());
        std::memcpy(out, text.data(), text.size());
        return {out, text.size()};
    }
    if (t.kind == kind_array) {
        const nb::ndarray<nb::ro> &view = buffer.keep.emplace(array_view(t, value, {name}));
        if (contiguous(view))
            return {static_cast<const std::byte *>(view.data()), t.size};
        std::byte *out = buffer.reserve(t.size);
        copy_array(view, out);
        return {out, t.size};
    }
    if (detail::is_collection(t.kind)) {
        std::byte *out = buffer.reserve(t.size);
        return {out, encode_collection(t, value, out, {name})};
    }
    std::byte *out = buffer.reserve(t.size);
    encode_into(t, value, out, {name});
    return {out, t.size};
}

void Types::encode_to(std::uint32_t index, PyObject *value, std::span<std::byte> out) const {
    const detail::type_ref &t = field(index);
    if (out.size() != t.size)
        raise(PyExc_ValueError, labels_[index] + ": the destination has the wrong size");
    std::vector<PendingArray> pending;
    encode_into(t, value, out.data(), {labels_[index], nullptr, &pending});
    // An array's source may lie in out, even at another array's place, so every such source is copied aside
    // before any array is written. The other bytes are already in out, read before any array was written.
    std::vector<bool> inside(pending.size());
    std::size_t total = 0;
    std::size_t aside = 0;
    for (std::size_t i = 0; i < pending.size(); ++i) {
        inside[i] = overlaps(pending[i].view, out.data(), out.size());
        total += pending[i].view.nbytes();
        aside += inside[i] ? pending[i].view.nbytes() : 0;
    }
    std::unique_ptr<std::byte[]> copies(aside == 0 ? nullptr : new std::byte[aside]);
    auto write = [&] {
        std::byte *next = copies.get();
        for (std::size_t i = 0; i < pending.size(); ++i)
            if (inside[i]) {
                gather(pending[i].view, next);
                next += pending[i].view.nbytes();
            }
        next = copies.get();
        for (std::size_t i = 0; i < pending.size(); ++i) {
            if (!inside[i]) {
                gather(pending[i].view, pending[i].out);
                continue;
            }
            std::memcpy(pending[i].out, next, pending[i].view.nbytes());
            next += pending[i].view.nbytes();
        }
    };
    if (total + aside >= detail::large_copy) {
        nb::gil_scoped_release unlocked;
        write();
    } else {
        write();
    }
}

nb::object Types::decode(std::uint32_t index, std::span<const std::byte> bytes,
                         std::unique_ptr<std::byte[]> *owned) const {
    const detail::type_ref &t = field(index);
    const std::string &name = labels_[index];
    if (t.kind == kind_array) {
        if (bytes.size() != t.size)
            raise(PyExc_ValueError, name + ": the stored array has the wrong size");
        return decode_array(t, bytes.data(), owned);
    }
    // A list, set or dict is read as its length and the slots it uses, so it may be shorter than its size.
    const bool fits = detail::prefixed(t.kind)        ? bytes.size() <= t.size - 4
                      : detail::is_collection(t.kind) ? bytes.size() <= t.size
                                                      : bytes.size() == t.size;
    if (!fits)
        raise(PyExc_ValueError, name + ": " + std::to_string(bytes.size()) +
                                    " bytes do not fit a value that takes " + std::to_string(t.size));
    if (detail::is_collection(t.kind)) {
        const auto length = bytes.size() >= 4 ? load<std::uint32_t>(bytes.data()) : 0u;
        const detail::type_node &node = tree_.node(t.node);
        if (bytes.size() < 4 || length > node.capacity ||
            bytes.size() < node.slots + std::uint64_t{length} * node.stride)
            corrupt(name);
        // A list or set read as a NativeCollection or NativeSet takes over the read's heap buffer, as an array
        // does.
        const NodeInfo &n = nodes_[t.node];
        if (n.stored && owned != nullptr && owned->get() == bytes.data()) {
            if (t.kind == kind_list)
                return SlotStore::make<NativeCollection>(n.cls, nb::find(*this), t, name, std::move(*owned),
                                                         bytes.size());
            return SlotStore::make<NativeSet>(n.cls, nb::find(*this), t, name, std::move(*owned), bytes.size());
        }
    }
    if (t.kind <= kind_ref) {
        if (t.kind == kind_bytes && field_is_bytearray(index))
            return nb::steal(PyByteArray_FromStringAndSize(reinterpret_cast<const char *>(bytes.data()),
                                                           static_cast<Py_ssize_t>(bytes.size())));
        PyObject *value =
            sharedbox::decode(desc_of(t), reinterpret_cast<const char *>(bytes.data()), bytes.size());
        if (value == nullptr)
            throw nb::python_error();
        if (const Conversion *c = conversion(t))
            return through(c->read_as, nb::steal(value));
        return nb::steal(value);
    }
    if (t.kind == kind_decimal) {
        PyObject *value = scalars::decode(kind_decimal, bytes, name);
        if (value == nullptr)
            throw nb::python_error();
        if (const Conversion *c = conversion(t))
            return through(c->read_as, nb::steal(value));
        return nb::steal(value);
    }
    return decode_from(t, bytes.data(), field_container, index, {name});
}

PyObject *Types::element_annotation(const detail::type_ref &t) const { return item(nodes_[t.node].info, 2); }

std::size_t Types::encode_collection(const detail::type_ref &t, PyObject *value, std::byte *out,
                                     const Where &where) const {
    const bool list = t.kind == kind_list && PyObject_TypeCheck(value, collection_type_);
    if (list || (t.kind == kind_set && PyObject_TypeCheck(value, set_type_))) {
        if (!nb::inst_ready(value))
            raise(PyExc_TypeError, where.name + " got a " +
                                       type_name(reinterpret_cast<PyObject *>(Py_TYPE(value))) +
                                       " that was never built");
        const SlotStore &stored = list ? static_cast<const SlotStore &>(*nb::inst_ptr<NativeCollection>(value))
                                       : *nb::inst_ptr<NativeSet>(value);
        if (stored.has_elements(nodes_[t.node].element_text, element_annotation(t)))
            return stored.copy_to(out, tree_.node(t.node).capacity, where.name);
    }
    if (!accepts(t, value))
        wrong(where.name, t.kind == kind_dict ? "a mapping" : t.kind == kind_set ? "a set" : "a sequence", value);
    const detail::type_node &node = tree_.node(t.node);
    const nb::object items = snapshot(value, t.kind);
    const Py_ssize_t length = length_of(items.ptr());
    if (length > static_cast<Py_ssize_t>(node.capacity))
        raise(PyExc_ValueError, where.name + " holds at most " + std::to_string(node.capacity) +
                                    " elements; the value has " + std::to_string(length));
    put(out, static_cast<std::uint32_t>(length));
    // Set elements are compared by their bytes, and out may hold an older value, as a stream slot does, so the
    // slots are zeroed first: the bytes an element leaves unwritten, such as padding, then read as zero.
    if (t.kind == kind_set)
        std::memset(out + node.slots, 0, static_cast<std::size_t>(length) * node.stride);
    const detail::type_member &first = tree_.member(node.members);
    for (Py_ssize_t i = 0; i < length; ++i) {
        std::byte *slot = out + node.slots + static_cast<std::size_t>(i) * node.stride;
        PyObject *element = at(items.ptr(), i);
        if (t.kind != kind_dict) {
            encode_into(first.type, element, slot, where);
            continue;
        }
        const detail::type_member &value_member = tree_.member(node.members + 1);
        if (!PyTuple_Check(element) || PyTuple_Size(element) != 2)
            wrong(where.name, "a mapping whose items() are (key, value) pairs", value);
        encode_into(first.type, PyTuple_GetItem(element, 0), slot, where);
        encode_into(value_member.type, PyTuple_GetItem(element, 1), slot + value_member.offset, where);
    }
    return node.slots + static_cast<std::size_t>(length) * node.stride;
}

void Types::encode_into(const detail::type_ref &t, PyObject *value, std::byte *out, const Where &where) const {
    if (const Conversion *c = conversion(t); c != nullptr && !c->write_as.is_none()) [[unlikely]]
        return encode_converted(*c, t, value, out, where);
    encode_plain(t, value, out, where);
}

void Types::encode_converted(const Conversion &c, const detail::type_ref &t, PyObject *value, std::byte *out,
                             const Where &where) const {
    nb::object converted;
    try {
        converted = through(c.write_as, nb::borrow(value));
    } catch (nb::python_error &error) {
        // The conversion's message starts with a verb; the field's name goes in front of it.
        if (!error.matches(PyExc_TypeError) && !error.matches(PyExc_OverflowError))
            throw;
        raise(error.type().ptr(), where.name + " " + nb::str(error.value()).c_str());
    }
    encode_plain(t, converted.ptr(), out, where);
}

void Types::encode_plain(const detail::type_ref &t, PyObject *value, std::byte *out, const Where &where) const {
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
    if (is_scalar(t.kind)) {
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
        // out may hold an older value, as a stream slot does, so absence is written, not assumed.
        if (value == Py_None) {
            out[0] = std::byte{0};
            return;
        }
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
        // A number goes to the first member of its own family, which raises OverflowError if it does not
        // fit, rather than to a wider member of another family.
        if (chosen < 0) {
            const family f = value_family(value);
            for (std::uint32_t tag = 0; f != family::none && tag < node.count; ++tag)
                if (kind_family(tree_.member(node.members + tag).type.kind) == f) {
                    chosen = static_cast<int>(tag);
                    break;
                }
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
    case kind_list:
    case kind_set:
    case kind_dict:
        encode_collection(t, value, out, where);
        return;
    case kind_array: {
        const nb::ndarray<nb::ro> view = array_view(t, value, where);
        // The arrays of a reserved stream slot already are their destination.
        if (static_cast<const std::byte *>(view.data()) == out && contiguous(view))
            return;
        if (where.pending != nullptr)
            where.pending->push_back({view, out});
        else
            copy_array(view, out);
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
                    out[member(i).offset] = std::byte{0};
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
    nb::object value = decode_stored(t, data, container, member, where);
    if (const Conversion *c = conversion(t))
        return through(c->read_as, std::move(value));
    return value;
}

nb::object Types::decode_stored(const detail::type_ref &t, const std::byte *data, std::uint32_t container,
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
    if (is_scalar(t.kind)) {
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
    // A member of a collection or record as a new reference; a bool, int, float or str skips decode_from.
    auto element = [&](const detail::type_ref &m, const std::byte *at, std::uint32_t index) -> PyObject * {
        if (where.given != nullptr && (t.kind == kind_record || t.kind == kind_tuple)) {
            const auto found = where.given->find(bytearray_key(t.node, index));
            if (found != where.given->end())
                return Py_NewRef(found->second);
        }
        if (m.kind > kind_str || !conversions_.empty())
            return decode_from(m, at, t.node, index, where).release().ptr();
        PyObject *value = decode_plain(m, at, where.name);
        if (value == nullptr)
            throw nb::python_error();
        return value;
    };
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
        if (made == nullptr) {
            // A Flag with the STRICT boundary raises ValueError for bits it does not declare.
            if (!PyErr_ExceptionMatches(PyExc_ValueError))
                throw nb::python_error();
            PyErr_Clear();
            corrupt(where.name);
        }
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
    case kind_list:
    case kind_set:
    case kind_dict: {
        const auto length = load<std::uint32_t>(data);
        if (length > node.capacity)
            corrupt(where.name);
        const detail::type_member &first = tree_.member(node.members);
        if (t.kind == kind_dict) {
            const detail::type_member &value_member = tree_.member(node.members + 1);
            nb::dict out;
            for (std::uint32_t i = 0; i < length; ++i) {
                const std::byte *slot = data + node.slots + std::size_t{i} * node.stride;
                nb::object key = nb::steal(element(first.type, slot, 0));
                nb::object value = nb::steal(element(value_member.type, slot + value_member.offset, 1));
                if (PyDict_SetItem(out.ptr(), key.ptr(), value.ptr()) != 0)
                    throw nb::python_error();
            }
            return out;
        }
        if (n.stored) {
            const std::span<const std::byte> stored(data, node.slots + std::size_t{length} * node.stride);
            if (t.kind == kind_list)
                return SlotStore::make<NativeCollection>(n.cls, nb::find(*this), t, where.name, stored);
            return SlotStore::make<NativeSet>(n.cls, nb::find(*this), t, where.name, stored);
        }
        auto slot = [&](std::uint32_t i) { return data + node.slots + std::size_t{i} * node.stride; };
        const bool is_list = n.cls == reinterpret_cast<PyObject *>(&PyList_Type);
        if (is_list || n.cls == reinterpret_cast<PyObject *>(&PyTuple_Type)) {
            nb::object out = nb::steal(is_list ? PyList_New(length) : PyTuple_New(length));
            if (!out.is_valid())
                throw nb::python_error();
            for (std::uint32_t i = 0; i < length; ++i)
                store_new_item(out.ptr(), is_list, i, element(first.type, slot(i), 0));
            return out;
        }
        // PySet_Add may fill a frozenset that no other code has seen yet.
        const bool frozen = n.cls == reinterpret_cast<PyObject *>(&PyFrozenSet_Type);
        nb::object out = nb::steal(frozen ? PyFrozenSet_New(nullptr) : PySet_New(nullptr));
        if (!out.is_valid())
            throw nb::python_error();
        for (std::uint32_t i = 0; i < length; ++i) {
            nb::object item = nb::steal(element(first.type, slot(i), 0));
            if (PySet_Add(out.ptr(), item.ptr()) != 0)
                throw nb::python_error();
        }
        return out;
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
                    nb::object value = nb::steal(element(m.type, data + m.offset, i));
                    if (PyDict_SetItem(out.ptr(), n.attrs[i], value.ptr()) != 0)
                        throw nb::python_error();
                    continue;
                }
                const auto present = load<std::uint8_t>(data + m.offset);
                if (present > 1)
                    corrupt(where.name);
                if (present == 1) {
                    const detail::type_member &inner = tree_.member(tree_.node(m.type.node).members);
                    nb::object value =
                        decode_from(inner.type, data + m.offset + inner.offset, m.type.node, 0, where);
                    if (PyDict_SetItem(out.ptr(), n.attrs[i], value.ptr()) != 0)
                        throw nb::python_error();
                }
            }
            return out;
        }
        if (n.form == 0 && n.by_position) {
            // The entries start as nullptr and the guard releases every one, so a member that fails to
            // decode releases the ones before it. Without keywords, a class that has no vectorcall of its
            // own gets a tuple from CPython, which its __init__ unpacks without a dict.
            Scratch<PyObject *, 16> args(node.count);
            struct Release {
                std::span<PyObject *> held;
                ~Release() {
                    for (PyObject *p : held)
                        Py_XDECREF(p);
                }
            } release{args.span()};
            for (std::uint32_t i = 0; i < node.count; ++i)
                args[i] = element(member(i).type, data + member(i).offset, i);
            PyObject *made = PyObject_Vectorcall(n.cls, args.span().data(), node.count, nullptr);
            return nb::steal(owned(made));
        }
        if (n.form == 0) {
            nb::dict kwargs;
            for (std::uint32_t i = 0; i < node.count; ++i)
                kwargs[nb::handle(n.keywords[i])] = nb::steal(element(member(i).type, data + member(i).offset, i));
            PyObject *made = PyObject_Call(n.cls, nb::tuple().ptr(), kwargs.ptr());
            return nb::steal(owned(made));
        }
        nb::object items = nb::steal(PyTuple_New(node.count));
        if (!items.is_valid())
            throw nb::python_error();
        for (std::uint32_t i = 0; i < node.count; ++i)
            store_new_item(items.ptr(), false, i, element(member(i).type, data + member(i).offset, i));
        if (n.form == 2)
            return items;
        PyObject *made = PyObject_CallObject(n.cls, items.ptr());
        return nb::steal(owned(made));
    }
    case kind_array:
        return decode_array(t, data, nullptr);
    default:
        raise(PyExc_TypeError,
              where.name + ": values of kind " + std::to_string(t.kind) + " are not supported yet");
    }
}

nb::object Types::decode_element(const detail::type_ref &list, const std::byte *slot,
                                 const std::string &label) const {
    const detail::type_ref &m = tree_.member(tree_.node(list.node).members).type;
    if (m.kind <= kind_str && conversions_.empty())
        return nb::steal(owned(decode_plain(m, slot, label)));
    return decode_from(m, slot, list.node, 0, {label});
}

void Types::encode_element(const detail::type_ref &list, PyObject *value, std::byte *slot,
                           const std::string &label) const {
    encode_into(tree_.member(tree_.node(list.node).members).type, value, slot, {label});
}

nb::ndarray<nb::ro> Types::array_view(const detail::type_ref &t, PyObject *value, const Where &where) const {
    const NodeInfo &n = nodes_[t.node];
    const detail::type_node &node = tree_.node(t.node);
    nb::ndarray<nb::ro> view;
    const bool imported = nb::try_cast(nb::handle(value), view, false);
    if (imported && view.device_type() != nb::device::cpu::value)
        wrong(where.name, "an array in CPU memory", value);
    if (!imported || view.dtype() != nb::dlpack::dtype{node.dtype.code, node.dtype.bits, node.dtype.lanes}) {
        // numpy exports no DLPack type for bfloat16, so an ml_dtypes bfloat16 array is taken as its uint16 view.
        if (!n.as_uint16 || !numpy_bfloat16(value))
            wrong(where.name,
                  imported ? "an array of the field's DType" : "an array with __dlpack__ or the buffer protocol",
                  value);
        const nb::object bits = nb::steal(PyObject_CallMethod(value, "view", "s", "uint16"));
        if (!bits.is_valid())
            throw nb::python_error();
        if (!nb::try_cast(bits, view, false) || view.device_type() != nb::device::cpu::value ||
            view.dtype() != nb::dlpack::dtype{1, 16, 1})
            wrong(where.name, "an array of the field's DType", value);
    }
    bool same = view.ndim() == node.ndim;
    for (std::size_t i = 0; same && i < node.ndim; ++i)
        same = view.shape(i) == tree_.number(node.numbers + static_cast<std::uint32_t>(i));
    if (!same)
        raise(PyExc_ValueError, where.name + " holds an array of the field's Shape; the value's shape differs");
    return view;
}

nb::ndarray<nb::c_contig, nb::device::cpu> Types::array_out_of(const detail::type_ref &t, const std::string &name,
                                                               PyObject *out) const {
    const NodeInfo &n = nodes_[t.node];
    const detail::type_node &node = tree_.node(t.node);
    nb::dlpack::dtype want{node.dtype.code, node.dtype.bits, node.dtype.lanes};
    nb::ndarray<nb::c_contig, nb::device::cpu> view;
    if (!nb::try_cast(nb::handle(out), view, false)) {
        // numpy exports no DLPack type for bfloat16, so an ml_dtypes bfloat16 array is filled through its uint16
        // view.
        nb::object bits;
        if (n.as_uint16 && numpy_bfloat16(out)) {
            bits = nb::steal(PyObject_CallMethod(out, "view", "s", "uint16"));
            if (!bits.is_valid())
                throw nb::python_error();
            want = {1, 16, 1};
        }
        if (!bits.is_valid() || !nb::try_cast(bits, view, false))
            refuse_out(name, bits.is_valid() ? bits : nb::handle(out));
    }
    if (view.dtype() != want)
        raise(PyExc_ValueError,
              name + ": out has dtype " + dtype_text(view.dtype()) + ", the field holds " + dtype_text(want));
    bool same = view.ndim() == node.ndim;
    for (std::size_t i = 0; same && i < node.ndim; ++i)
        same = view.shape(i) == tree_.number(node.numbers + static_cast<std::uint32_t>(i));
    if (!same)
        raise(PyExc_ValueError, name + ": out has shape " +
                                    shape_text(view.ndim(), [&](std::size_t i) { return view.shape(i); }) +
                                    ", the field holds " + shape_text(node.ndim, [&](std::size_t i) {
                                        return tree_.number(node.numbers + static_cast<std::uint32_t>(i));
                                    }));
    return view;
}

nb::ndarray<nb::c_contig, nb::device::cpu> Types::array_out(std::uint32_t index, PyObject *out) const {
    const detail::type_ref &t = field(index);
    const std::string &name = labels_[index];
    if (t.kind != kind_array)
        raise(PyExc_TypeError, name + " is not an array field");
    return array_out_of(t, name, out);
}

Types::ArrayTarget Types::array_target(std::uint32_t index, std::span<const std::uint32_t> path,
                                       PyObject *out) const {
    detail::type_ref t = field(index);
    std::string name = labels_[index];
    std::uint64_t offset = 0;
    std::uint64_t key = field_container;
    for (const std::uint32_t step : path) {
        if (t.kind != kind_record && t.kind != kind_tuple)
            raise(PyExc_TypeError,
                  name + " has no array at that place; arrays are reached through records and tuples only");
        const detail::type_node &node = tree_.node(t.node);
        if (step >= node.count)
            raise(PyExc_TypeError, name + " has no member " + std::to_string(step));
        const detail::type_member &m = tree_.member(node.members + step);
        key = bytearray_key(t.node, step);
        offset += m.offset;
        const NodeInfo &n = nodes_[t.node];
        if (t.kind == kind_record && n.form <= 1)
            name += std::string(".") + nb::borrow<nb::str>(n.attrs[step]).c_str();
        else
            name += "[" + std::to_string(step) + "]";
        t = m.type;
    }
    if (t.kind != kind_array)
        raise(PyExc_TypeError, name + " is not an array");
    return {array_out_of(t, name, out), offset, t.size, key};
}

nb::object Types::decode_given(std::uint32_t index, std::span<const std::byte> bytes,
                               const std::unordered_map<std::uint64_t, PyObject *> &given) const {
    const detail::type_ref &t = field(index);
    const std::string &name = labels_[index];
    if (bytes.size() != t.size)
        raise(PyExc_ValueError, name + ": the stored value has the wrong size");
    return decode_from(t, bytes.data(), field_container, index, {name, &given});
}

void Types::copy_array(const nb::ndarray<nb::ro> &view, std::byte *out) {
    if (view.nbytes() >= detail::large_copy) {
        nb::gil_scoped_release unlocked;
        gather(view, out);
    } else {
        gather(view, out);
    }
}

nb::object Types::decode_array(const detail::type_ref &t, const std::byte *data,
                               std::unique_ptr<std::byte[]> *owned) const {
    std::unique_ptr<std::byte[]> buffer;
    if (owned != nullptr && owned->get() == data) {
        buffer = std::move(*owned);
    } else {
        buffer.reset(new std::byte[t.size]);
        if (t.size >= detail::large_copy) {
            nb::gil_scoped_release unlocked;
            std::memcpy(buffer.get(), data, t.size);
        } else {
            std::memcpy(buffer.get(), data, t.size);
        }
    }
    std::byte *raw = buffer.get();
    nb::capsule owner(raw, [](void *p) noexcept { delete[] static_cast<std::byte *>(p); });
    // Released only once the capsule exists, so a failure to make it does not leak the buffer.
    buffer.release();
    return wrap_array(t, raw, owner);
}

nb::object Types::wrap_array(const detail::type_ref &t, std::byte *raw, nb::handle owner) const {
    const NodeInfo &n = nodes_[t.node];
    const detail::type_node &node = tree_.node(t.node);
    std::size_t shape[8];
    for (std::uint32_t i = 0; i < node.ndim; ++i)
        shape[i] = static_cast<std::size_t>(tree_.number(node.numbers + i));
    const nb::dlpack::dtype dtype = n.as_uint16
                                        ? nb::dlpack::dtype{1, 16, 1}
                                        : nb::dlpack::dtype{node.dtype.code, node.dtype.bits, node.dtype.lanes};
    // array_api makes a nanobind.nb_ndarray with __dlpack__; a framework-less ndarray casts to a bare capsule.
    nb::object array = nb::cast(nb::ndarray<nb::array_api>(raw, node.ndim, shape, owner, nullptr, dtype));
    if (n.cls == Py_None)
        return array;
    PyObject *made = PyObject_CallFunctionObjArgs(n.cls, array.ptr(), nullptr);
    return nb::steal(owned(made));
}

nb::object Types::array_over(std::uint32_t index, std::byte *data, nb::handle owner) const {
    const detail::type_ref &t = field(index);
    if (t.kind != kind_array)
        raise(PyExc_TypeError, labels_[index] + " is not an array field");
    return wrap_array(t, data, owner);
}

bool Types::holds_array(const detail::type_ref &t) const {
    if (t.kind == kind_array)
        return true;
    if (t.kind != kind_record && t.kind != kind_tuple)
        return false;
    const detail::type_node &node = tree_.node(t.node);
    for (std::uint32_t i = 0; i < node.count; ++i)
        if (holds_array(tree_.member(node.members + i).type))
            return true;
    return false;
}

nb::object Types::with_arrays(std::uint32_t index, PyObject *template_item, std::byte *data,
                              nb::handle owner) const {
    const detail::type_ref &t = field(index);
    if (template_item == Py_None) {
        if (t.kind != kind_array)
            raise(PyExc_TypeError, labels_[index] + " is not an array, so writing needs a template item");
    } else if (t.kind == kind_array) {
        // The template's array is replaced, never read, so any shape and dtype will do.
        nb::ndarray<nb::ro> view;
        if (!nb::try_cast(nb::handle(template_item), view, false) && !numpy_bfloat16(template_item))
            wrong(labels_[index], "an array with __dlpack__ or the buffer protocol", template_item);
    } else if (!holds_array(t)) {
        // A template that leads to no array is the item itself, so it is checked as sending it would be.
        std::unique_ptr<std::byte[]> scratch(new std::byte[t.size]);
        encode_into(t, template_item, scratch.get(), {labels_[index]});
    }
    return rebuild(t, template_item, data, owner, {labels_[index]});
}

nb::object Types::rebuild(const detail::type_ref &t, PyObject *value, std::byte *data, nb::handle owner,
                          const Where &where) const {
    if (t.kind == kind_array)
        return wrap_array(t, data, owner);
    if (!holds_array(t))
        return nb::borrow(value);
    const NodeInfo &n = nodes_[t.node];
    const detail::type_node &node = tree_.node(t.node);
    auto member = [&](std::uint32_t i) -> const detail::type_member & { return tree_.member(node.members + i); };
    if (n.form == 3) {
        if (!instance(value, scalars::mapping()))
            wrong(where.name, "a mapping with the TypedDict's keys", value);
        nb::dict out;
        if (PyDict_Update(out.ptr(), value) != 0)
            throw nb::python_error();
        for (std::uint32_t i = 0; i < node.count; ++i) {
            const detail::type_member &m = member(i);
            if (!holds_array(m.type))
                continue;
            PyObject *v = PyDict_GetItemWithError(out.ptr(), n.attrs[i]);
            if (v == nullptr && PyErr_Occurred())
                throw nb::python_error();
            // An array member's value is replaced, never read, so only a record or tuple member must be there.
            if (v == nullptr && m.type.kind != kind_array)
                raise(PyExc_TypeError,
                      where.name + " needs key '" + std::string(nb::borrow<nb::str>(n.attrs[i]).c_str()) + "'");
            out[nb::handle(n.attrs[i])] = rebuild(m.type, v, data + m.offset, owner, where);
        }
        return out;
    }
    if (n.form == 2 ? !PyTuple_Check(value) : !instance(value, n.cls))
        wrong(where.name, n.form == 2 ? std::string("a tuple") : "a " + type_name(n.cls), value);
    if (n.form != 0 && PyTuple_Size(value) != node.count)
        raise(PyExc_ValueError, where.name + " holds a tuple of " + std::to_string(node.count) +
                                    " items; the value has " + std::to_string(PyTuple_Size(value)));
    nb::object items = nb::steal(PyTuple_New(node.count));
    if (!items.is_valid())
        throw nb::python_error();
    for (std::uint32_t i = 0; i < node.count; ++i) {
        nb::object v = n.form == 0 ? nb::steal(PyObject_GetAttr(value, n.attrs[i]))
                                   : nb::borrow(PyTuple_GetItem(value, static_cast<Py_ssize_t>(i)));
        if (!v.is_valid())
            throw nb::python_error();
        const detail::type_member &m = member(i);
        if (holds_array(m.type))
            v = rebuild(m.type, v.ptr(), data + m.offset, owner, where);
        PyTuple_SetItem(items.ptr(), static_cast<Py_ssize_t>(i), v.release().ptr());
    }
    if (n.form == 2)
        return items;
    if (n.form == 1) {
        PyObject *made = PyObject_CallObject(n.cls, items.ptr());
        return nb::steal(owned(made));
    }
    nb::dict kwargs;
    for (std::uint32_t i = 0; i < node.count; ++i)
        kwargs[nb::handle(n.keywords[i])] = nb::borrow(PyTuple_GetItem(items.ptr(), static_cast<Py_ssize_t>(i)));
    PyObject *made = PyObject_Call(n.cls, nb::tuple().ptr(), kwargs.ptr());
    return nb::steal(owned(made));
}

bool Types::accepts(const detail::type_ref &t, PyObject *value) const {
    switch (t.kind) {
    case kind_bool:
        return PyBool_Check(value) || scalars::is_numpy_bool(value);
    case kind_int:
        return scalars::is_integer(value);
    case kind_float:
        return scalars::is_real(value);
    case kind_str:
        return PyUnicode_Check(value);
    case kind_bytes:
        return PyBytes_Check(value) || PyByteArray_Check(value) || PyMemoryView_Check(value);
    default:
        break;
    }
    if (t.kind >= kind_complex && t.kind <= kind_datetime64)
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
    case kind_list:
        return instance(value, scalars::sequence()) && !PyUnicode_Check(value) && !PyBytes_Check(value) &&
               !PyByteArray_Check(value);
    case kind_set:
        return instance(value, scalars::set());
    case kind_dict:
        return instance(value, scalars::mapping());
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
