#include <nanobind/nanobind.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>

#include <concepts>
#include <cstddef>
#include <cstring>
#include <exception>
#include <iterator>
#include <memory>
#include <new>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <type_traits>
#include <utility>

#include <sharedbox/sharedbox.hpp>

#if PY_VERSION_HEX < 0x030C0000
#include <structmember.h>
#endif

#include "codec.hpp"
#include "collection.hpp"
#include "dict.hpp"
#include "scalars.hpp"
#include "segment.hpp"
#include "set.hpp"
#include "stream.hpp"
#include "types.hpp"

namespace nb = nanobind;
using namespace nb::literals;
using sharedbox::Segment;

namespace {

using Values = std::vector<std::pair<std::uint32_t, nb::object>>;
using Encoded = std::vector<std::pair<std::uint32_t, std::string>>;

sharedbox::FieldKind to_kind(std::uint32_t code) {
    if (!sharedbox::detail::kind_known(code))
        throw std::invalid_argument("unknown field kind " + std::to_string(code));
    return static_cast<sharedbox::FieldKind>(code);
}

nb::object decode_value(const sharedbox::FieldDesc &field, std::string_view bytes) {
    PyObject *value = sharedbox::decode(field, bytes.data(), bytes.size());
    if (value == nullptr)
        throw nb::python_error();
    return nb::steal(value);
}

const sharedbox::Types *types_of(nb::handle types) {
    if (types.is_none())
        return nullptr;
    return &nb::cast<const sharedbox::Types &>(types);
}

// Decodes a field read with Segment::read; an array takes the read's heap buffer instead of copying it.
nb::object decode_read(const sharedbox::Types *types, std::uint32_t index, sharedbox::FieldRead &read) {
    const auto *data = reinterpret_cast<const std::byte *>(read.bytes.data());
    if (static_cast<std::uint8_t>(read.field->kind) <= sharedbox::kind_ref) {
        if (types != nullptr && types->field_via_types(index)) [[unlikely]]
            return types->decode(index, {data, read.bytes.size()}, nullptr);
        return decode_value(*read.field, read.bytes);
    }
    return sharedbox::need(types, index)
        .decode(index, {data, read.bytes.size()}, read.large ? &read.large : nullptr);
}

// A field inside a copy of the record made by with_record.
nb::object decode_payload(const sharedbox::Types *types, const sharedbox::FieldDesc &field, std::uint32_t index,
                          const std::byte *record) {
    const std::string_view bytes = sharedbox::payload(field, record);
    const auto data = std::as_bytes(std::span(bytes.data(), bytes.size()));
    if (static_cast<std::uint8_t>(field.kind) <= sharedbox::kind_ref) {
        if (types != nullptr && types->field_via_types(index)) [[unlikely]]
            return types->decode(index, data, nullptr);
        return decode_value(field, bytes);
    }
    return sharedbox::need(types, index).decode(index, data, nullptr);
}

nb::object get(const Segment &s, std::uint32_t index, const sharedbox::Types *types) {
    sharedbox::FieldRead read;
    s.read(index, read);
    return decode_read(types, index, read);
}

// Decodes only the create id of a reference field, not its name.
std::uint64_t stored_ref_id(const Segment &s, std::uint32_t index) {
    sharedbox::FieldRead read;
    s.read(index, read);
    if (read.field->kind != sharedbox::FieldKind::Ref)
        throw std::invalid_argument("field " + std::to_string(index) + " is not a reference");
    std::uint64_t create_id = 0;
    std::memcpy(&create_id, read.bytes.data(), sizeof create_id);
    return create_id;
}

// The last value decoded from one field and the write count it was decoded at.
struct CachedValue {
    nb::ft_mutex lock;
    std::uint64_t version = 0;
    PyObject *value = nullptr;
};

// The values a box's reads decoded from fields whose values cannot be changed, one slot per field.
// A box keeps it in its _values slot rather than in its Segment: the box's finalizer holds the
// Segment, so a value that refers back to the box would keep the box alive from there.
class ValueCache {
public:
    explicit ValueCache(std::uint32_t fields) : slots_(std::make_unique<CachedValue[]>(fields)), count_(fields) {}
    ~ValueCache() { clear(); }
    ValueCache(const ValueCache &) = delete;
    ValueCache &operator=(const ValueCache &) = delete;

    CachedValue &slot(std::uint32_t index) {
        sharedbox::check_index(index, count_);
        return slots_[index];
    }

    // A value's __del__ may read a field again, so each reference is dropped after its slot is unlocked.
    void clear() {
        for (std::uint32_t i = 0; i < count_; ++i) {
            PyObject *old = nullptr;
            {
                nb::ft_lock_guard guard(slots_[i].lock);
                old = std::exchange(slots_[i].value, nullptr);
            }
            Py_XDECREF(old);
        }
    }

    // The collector stops the other threads, one of which may hold a slot lock, so this takes none.
    int traverse(visitproc visit, void *arg) const {
        for (std::uint32_t i = 0; i < count_; ++i)
            Py_VISIT(slots_[i].value);
        return 0;
    }

    // fork() copies a slot lock another thread of the parent may hold, a thread the child does not have.
    void after_fork() {
        for (std::uint32_t i = 0; i < count_; ++i)
            new (&slots_[i].lock) nb::ft_mutex();
    }

private:
    std::unique_ptr<CachedValue[]> slots_;
    std::uint32_t count_;
};

// A reusable field's value: the one cache holds while the field's write count still equals the count
// it was decoded at, else a new decode, which replaces it. Decoding runs Python code (a record's
// __post_init__ may read this field), so no slot lock is held across it.
nb::object get_reusable(const Segment &s, ValueCache &cache, std::uint32_t index, const sharedbox::Types *types) {
    CachedValue &slot = cache.slot(index);
    const std::uint64_t now = s.version(index);
    {
        nb::ft_lock_guard guard(slot.lock);
        if (slot.value != nullptr && slot.version == now)
            return nb::borrow(slot.value);
    }
    sharedbox::FieldRead read;
    s.read(index, read);
    nb::object value = decode_read(types, index, read);
    PyObject *old = nullptr;
    {
        nb::ft_lock_guard guard(slot.lock);
        // A thread that decoded an older version never puts it back over a newer one, and none stores after
        // close().
        if (!s.closed() && (slot.value == nullptr || read.version >= slot.version)) {
            old = std::exchange(slot.value, Py_NewRef(value.ptr()));
            slot.version = read.version;
        }
    }
    Py_XDECREF(old);
    return value;
}

void set_one(Segment &s, std::uint32_t index, nb::handle value, const sharedbox::Types *types) {
    sharedbox::check_index(index, s.field_count());
    sharedbox::EncodeBuffer buffer;
    const sharedbox::FieldDesc &field = s.fields()[index];
    s.write_one(index, static_cast<std::uint8_t>(field.kind) <= sharedbox::kind_ref
                           ? sharedbox::encode(field, s.field_names()[index], value.ptr(), buffer)
                           : sharedbox::need(types, index).encode(index, value.ptr(), buffer));
}

// Both set in NB_MODULE.
PyTypeObject *segment_type = nullptr;
PyTypeObject *value_cache_type = nullptr;
PyObject *reraise = nullptr;
thread_local std::exception_ptr pending;

// Unlike a bound method, a type slot is reached for a Field made by Field.__new__ alone, whose C++
// object was never constructed.
bool field_ready(PyObject *self) noexcept {
    if (nb::inst_ready(self))
        return true;
    PyErr_SetString(PyExc_TypeError, "Field.__init__ was not called");
    return false;
}

// Type slots run outside nanobind's dispatch, which is what translates C++ exceptions for bound
// functions. Rethrowing from a bound function applies the same translators, so a field raises what
// Segment.get and Segment.set raise.
void set_error() noexcept {
    pending = std::current_exception();
    Py_XDECREF(PyObject_CallNoArgs(reraise));
}

struct Field {
    nb::object spec;
    std::uint32_t index;
    // SharedBox's _segment slot, and the function that reads it from a box.
    nb::object segment_slot;
    descrgetfunc read_slot;
    // The class's Types, or None; t points into it.
    nb::object types;
    const sharedbox::Types *t;
    // SharedBox's _values slot and the function that reads it; set only when reads of this field
    // reuse their values.
    nb::object values_slot;
    descrgetfunc read_values;
    bool reusable;
};

// A new reference to the Segment in box's _segment slot, or nullptr with an exception set: the
// AttributeError of an empty slot, as a Python descriptor reading box._segment would raise. A Segment
// made by Segment.__new__ alone has no C++ object behind it, so it counts as the wrong type.
PyObject *segment_of(PyObject *segment_slot, descrgetfunc read_slot, PyObject *box) {
    PyObject *segment = read_slot(segment_slot, box, reinterpret_cast<PyObject *>(Py_TYPE(box)));
    if (segment != nullptr && (Py_TYPE(segment) != segment_type || !nb::inst_ready(segment))) {
        Py_DECREF(segment);
        PyErr_SetString(PyExc_TypeError, "_segment does not hold a Segment");
        return nullptr;
    }
    return segment;
}

// A new reference to the ValueCache in box's _values slot, or nullptr with an exception set, as
// segment_of gives the Segment.
PyObject *values_of(PyObject *values_slot, descrgetfunc read_slot, PyObject *box) {
    PyObject *values = read_slot(values_slot, box, reinterpret_cast<PyObject *>(Py_TYPE(box)));
    if (values != nullptr && (Py_TYPE(values) != value_cache_type || !nb::inst_ready(values))) {
        Py_DECREF(values);
        PyErr_SetString(PyExc_TypeError, "_values does not hold a ValueCache");
        return nullptr;
    }
    return values;
}

PyObject *field_get(PyObject *self, PyObject *box, PyObject *) noexcept {
    if (box == nullptr || box == Py_None)
        return Py_NewRef(self);
    if (!field_ready(self))
        return nullptr;
    const Field &f = *nb::inst_ptr<Field>(self);
    const nb::object segment = nb::steal(segment_of(f.segment_slot.ptr(), f.read_slot, box));
    if (!segment.is_valid())
        return nullptr;
    try {
        const Segment &s = *nb::inst_ptr<Segment>(segment);
        if (!f.reusable)
            return get(s, f.index, f.t).release().ptr();
        const nb::object values = nb::steal(values_of(f.values_slot.ptr(), f.read_values, box));
        if (!values.is_valid())
            return nullptr;
        return get_reusable(s, *nb::inst_ptr<ValueCache>(values), f.index, f.t).release().ptr();
    } catch (...) {
        set_error();
        return nullptr;
    }
}

int field_set(PyObject *self, PyObject *box, PyObject *value) noexcept {
    if (value == nullptr) {
        PyErr_SetString(PyExc_AttributeError, "a SharedBox field cannot be deleted");
        return -1;
    }
    if (!field_ready(self))
        return -1;
    const Field &f = *nb::inst_ptr<Field>(self);
    const nb::object segment = nb::steal(segment_of(f.segment_slot.ptr(), f.read_slot, box));
    if (!segment.is_valid())
        return -1;
    try {
        set_one(*nb::inst_ptr<Segment>(segment), f.index, value, f.t);
        return 0;
    } catch (...) {
        set_error();
        return -1;
    }
}

// A class holds its Fields, and a Field reaches back to the class's module through its Types (a
// record type's generated methods keep the module's globals), so the collector must see them to free
// that cycle. There is no tp_clear: like a tuple's, a Field's references never change, and every
// cycle through one also passes through the class dict holding it, which the collector clears.
int field_traverse(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self));
    if (!nb::inst_ready(self))
        return 0;
    const Field &f = *nb::inst_ptr<Field>(self);
    Py_VISIT(f.spec.ptr());
    Py_VISIT(f.segment_slot.ptr());
    Py_VISIT(f.types.ptr());
    Py_VISIT(f.values_slot.ptr());
    return 0;
}

PyType_Slot field_slots[] = {{Py_tp_descr_get, reinterpret_cast<void *>(field_get)},
                             {Py_tp_descr_set, reinterpret_cast<void *>(field_set)},
                             {Py_tp_traverse, reinterpret_cast<void *>(field_traverse)},
                             {0, nullptr}};

// The limited API has no tuple macros; the full API's skip the checks the functions make.
Py_ssize_t tuple_size(PyObject *tuple) {
#ifdef Py_LIMITED_API
    return PyTuple_Size(tuple);
#else
    return PyTuple_GET_SIZE(tuple);
#endif
}

PyObject *tuple_item(PyObject *tuple, Py_ssize_t i) {
#ifdef Py_LIMITED_API
    return PyTuple_GetItem(tuple, i);
#else
    return PyTuple_GET_ITEM(tuple, i);
#endif
}

#if PY_VERSION_HEX < 0x030C0000
constexpr int member_ssize_t = T_PYSSIZET;
constexpr int member_readonly = READONLY;
#else
constexpr int member_ssize_t = Py_T_PYSSIZET;
constexpr int member_readonly = Py_READONLY;
#endif

// Set in NB_MODULE: types.MethodType, since PyMethod_New is not in the limited API.
PyObject *method_type = nullptr;
// Set in NB_MODULE: builtins.getattr, which a pickled BoxMethod is rebuilt with.
PyObject *getattr_function = nullptr;

// A SharedBox method in C, made for one class from its fields. A class holds its BoxMethod, whose
// specs can hold the class as a reference target, so the type supports the cycle collector.
struct BoxMethod {
    PyObject ob_base;
    vectorcallfunc vectorcall;
    int kind;
    PyObject *cls;          // the class it was made for, given as owner
    PyObject *qualname;     // the class's __qualname__, for messages
    PyObject *names;        // tuple of str, in field index order
    PyObject *specs;        // tuple: the FieldSpec of a reference field, else None
    PyObject *helper;       // update: _refs.stored; snapshot: _refs.box_ref
    PyObject *follow;       // snapshot: SharedBox._follow; None for update
    PyObject *fallback;     // SharedBox's Python method, for a box of another class
    PyObject *segment_slot; // SharedBox's _segment slot descriptor
    PyObject *types;        // the class's Types, or None
    const sharedbox::Types *t;
    descrgetfunc read_slot;
    bool has_refs; // whether any entry of specs is not None
};

enum BoxMethodKind { kind_update = 0, kind_snapshot = 1 };

using sharedbox::Scratch;

// The index of the field called name, or -1. Keyword names written in code are interned like the
// field names, so a pointer match finds them; a name built at run time needs the text compared.
Py_ssize_t field_index(const BoxMethod &m, PyObject *name) {
    const Py_ssize_t count = tuple_size(m.names);
    for (Py_ssize_t i = 0; i < count; ++i)
        if (tuple_item(m.names, i) == name)
            return i;
    for (Py_ssize_t i = 0; i < count; ++i)
        if (PyUnicode_Compare(tuple_item(m.names, i), name) == 0)
            return i;
    return -1;
}

PyObject *box_update(PyObject *callable, PyObject *const *args, std::size_t nargsf, PyObject *kwnames) noexcept {
    const BoxMethod &m = *reinterpret_cast<BoxMethod *>(callable);
    const Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
    if (nargs != 1) {
        PyErr_SetString(PyExc_TypeError, nargs == 0 ? "update() needs the box as its first argument"
                                                    : "update() takes no positional arguments");
        return nullptr;
    }
    // The names and specs are those of m.cls; a box of a subclass with more fields, or of an
    // unrelated class, needs its own class's layout, which the Python method reads.
    if (reinterpret_cast<PyObject *>(Py_TYPE(args[0])) != m.cls)
        return PyObject_Vectorcall(m.fallback, args, nargsf, kwnames);
    try {
        const nb::object segment = nb::steal(segment_of(m.segment_slot, m.read_slot, args[0]));
        if (!segment.is_valid())
            return nullptr;
        const std::size_t count = kwnames == nullptr ? 0 : static_cast<std::size_t>(tuple_size(kwnames));
        Scratch<sharedbox::Pending, 8> pending(count);
        Scratch<sharedbox::EncodeBuffer, 8> buffers(count);
        Scratch<sharedbox::value, 8> encoded(count);
        Scratch<nb::object, 8> owned(m.has_refs ? count : 0);
        // Every name is found before any value is converted, so unknown names are reported first.
        for (std::size_t k = 0; k < count; ++k) {
            const Py_ssize_t index = field_index(m, tuple_item(kwnames, static_cast<Py_ssize_t>(k)));
            // The Python method checks the names and handles metadata, so a name that is no field is left to
            // it; only those updates cost more.
            if (index < 0)
                return PyObject_Vectorcall(m.fallback, args, nargsf, kwnames);
            pending[k] = {static_cast<std::uint32_t>(index), args[1 + k]};
        }
        for (std::size_t k = 0; m.has_refs && k < count; ++k) {
            PyObject *spec = tuple_item(m.specs, pending[k].field);
            if (spec == Py_None)
                continue;
            // Kept until the write ends, since pending holds only a borrowed pointer to it.
            PyObject *const call[] = {spec, pending[k].value};
            owned[k] = nb::steal(PyObject_Vectorcall(m.helper, call, 2, nullptr));
            if (!owned[k].is_valid())
                return nullptr;
            pending[k].value = owned[k].ptr();
        }
        Segment &s = *nb::inst_ptr<Segment>(segment);
        sharedbox::encode_all(s, m.t, pending.span(), buffers.span(), encoded.span());
        s.write(encoded.span());
        Py_RETURN_NONE;
    } catch (...) {
        set_error();
        return nullptr;
    }
}

PyObject *box_snapshot(PyObject *callable, PyObject *const *args, std::size_t nargsf, PyObject *kwnames) noexcept {
    const BoxMethod &m = *reinterpret_cast<BoxMethod *>(callable);
    const Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
    if (nargs != 1) {
        PyErr_SetString(PyExc_TypeError, nargs == 0 ? "snapshot() needs the box as its first argument"
                                                    : "snapshot() takes no positional arguments");
        return nullptr;
    }
    // As in box_update, a box of another class needs its own class's layout.
    if (reinterpret_cast<PyObject *>(Py_TYPE(args[0])) != m.cls)
        return PyObject_Vectorcall(m.fallback, args, nargsf, kwnames);
    PyObject *follow = Py_False;
    for (Py_ssize_t k = 0; kwnames != nullptr && k < tuple_size(kwnames); ++k) {
        PyObject *name = tuple_item(kwnames, k);
        if (PyUnicode_CompareWithASCIIString(name, "follow") != 0) {
            PyErr_Format(PyExc_TypeError, "snapshot() got an unexpected keyword argument '%U'", name);
            return nullptr;
        }
        follow = args[1 + k];
    }
    try {
        const nb::object segment = nb::steal(segment_of(m.segment_slot, m.read_slot, args[0]));
        if (!segment.is_valid())
            return nullptr;
        const Segment &s = *nb::inst_ptr<Segment>(segment);
        // The Segment's own copy of the fields, not the handle's, which a close() may empty while this runs.
        const std::span<const sharedbox::FieldDesc> fields = s.fields();
        const nb::object values = sharedbox::with_record(s, [&](const std::byte *record) {
            nb::object out = nb::steal(PyDict_New());
            if (!out.is_valid())
                throw nb::python_error();
            for (Py_ssize_t i = 0; i < tuple_size(m.names); ++i) {
                const auto index = static_cast<std::uint32_t>(i);
                sharedbox::check_index(index, fields.size());
                nb::object value = decode_payload(m.t, fields[index], index, record);
                PyObject *spec = tuple_item(m.specs, i);
                if (spec != Py_None) {
                    PyObject *const call[] = {value.ptr()};
                    value = nb::steal(PyObject_Vectorcall(m.helper, call, 1, nullptr));
                    if (!value.is_valid())
                        throw nb::python_error();
                }
                if (PyDict_SetItem(out.ptr(), tuple_item(m.names, i), value.ptr()) < 0)
                    throw nb::python_error();
            }
            return out;
        });
        if (m.has_refs) {
            const int truth = PyObject_IsTrue(follow);
            if (truth < 0)
                return nullptr;
            if (truth > 0) {
                PyObject *const call[] = {args[0], values.ptr()};
                PyObject *result = PyObject_Vectorcall(m.follow, call, 2, nullptr);
                if (result == nullptr)
                    return nullptr;
                Py_DECREF(result);
            }
        }
        return Py_NewRef(values.ptr());
    } catch (...) {
        set_error();
        return nullptr;
    }
}

PyObject *box_method_new(PyTypeObject *type, PyObject *args, PyObject *kwargs) noexcept {
    static const char *keywords[] = {"kind",   "owner",    "qualname",     "names", "specs", "helper",
                                     "follow", "fallback", "segment_slot", "types", nullptr};
    int kind = 0;
    PyObject *cls = nullptr, *qualname = nullptr, *names = nullptr, *specs = nullptr, *helper = nullptr,
             *follow = nullptr, *fallback = nullptr, *segment_slot = nullptr, *types = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "iO!UO!O!OOOO|O:BoxMethod", const_cast<char **>(keywords),
                                     &kind, &PyType_Type, &cls, &qualname, &PyTuple_Type, &names, &PyTuple_Type,
                                     &specs, &helper, &follow, &fallback, &segment_slot, &types))
        return nullptr;
    const sharedbox::Types *t = nullptr;
    if (types != Py_None) {
        if (!nb::isinstance<sharedbox::Types>(types) || !nb::inst_ready(types)) {
            PyErr_SetString(PyExc_TypeError, "types must be a Types or None");
            return nullptr;
        }
        t = nb::inst_ptr<sharedbox::Types>(types);
    }
    if (kind != kind_update && kind != kind_snapshot) {
        PyErr_Format(PyExc_ValueError, "unknown BoxMethod kind %d", kind);
        return nullptr;
    }
    if (tuple_size(names) != tuple_size(specs)) {
        PyErr_SetString(PyExc_ValueError, "names and specs differ in length");
        return nullptr;
    }
    for (Py_ssize_t i = 0; i < tuple_size(names); ++i)
        if (!PyUnicode_Check(tuple_item(names, i))) {
            PyErr_SetString(PyExc_TypeError, "names must hold only str");
            return nullptr;
        }
    if (!PyCallable_Check(helper) || !PyCallable_Check(fallback) ||
        (kind == kind_snapshot && !PyCallable_Check(follow))) {
        PyErr_SetString(PyExc_TypeError, "helper, fallback and the follow of a snapshot must be callable");
        return nullptr;
    }
    auto read_slot = reinterpret_cast<descrgetfunc>(PyType_GetSlot(Py_TYPE(segment_slot), Py_tp_descr_get));
    if (read_slot == nullptr) {
        PyErr_SetString(PyExc_TypeError, "segment_slot must be a descriptor");
        return nullptr;
    }
    auto alloc = reinterpret_cast<allocfunc>(PyType_GetSlot(type, Py_tp_alloc));
    auto *self = reinterpret_cast<BoxMethod *>(alloc(type, 0));
    if (self == nullptr)
        return nullptr;
    self->vectorcall = kind == kind_update ? box_update : box_snapshot;
    self->kind = kind;
    self->cls = Py_NewRef(cls);
    self->qualname = Py_NewRef(qualname);
    self->names = Py_NewRef(names);
    self->specs = Py_NewRef(specs);
    self->helper = Py_NewRef(helper);
    self->follow = Py_NewRef(follow);
    self->fallback = Py_NewRef(fallback);
    self->segment_slot = Py_NewRef(segment_slot);
    self->types = Py_NewRef(types);
    self->t = t;
    self->read_slot = read_slot;
    self->has_refs = false;
    for (Py_ssize_t i = 0; i < tuple_size(specs); ++i)
        self->has_refs = self->has_refs || tuple_item(specs, i) != Py_None;
    return reinterpret_cast<PyObject *>(self);
}

int box_method_traverse(PyObject *self, visitproc visit, void *arg) {
    const auto *m = reinterpret_cast<BoxMethod *>(self);
    Py_VISIT(Py_TYPE(self));
    Py_VISIT(m->cls);
    Py_VISIT(m->qualname);
    Py_VISIT(m->names);
    Py_VISIT(m->specs);
    Py_VISIT(m->helper);
    Py_VISIT(m->follow);
    Py_VISIT(m->fallback);
    Py_VISIT(m->segment_slot);
    Py_VISIT(m->types);
    return 0;
}

int box_method_clear(PyObject *self) {
    auto *m = reinterpret_cast<BoxMethod *>(self);
    // Cleared first, since t is the Types inside types, which a decref below can free.
    m->t = nullptr;
    Py_CLEAR(m->cls);
    Py_CLEAR(m->qualname);
    Py_CLEAR(m->names);
    Py_CLEAR(m->specs);
    Py_CLEAR(m->helper);
    Py_CLEAR(m->follow);
    Py_CLEAR(m->fallback);
    Py_CLEAR(m->segment_slot);
    Py_CLEAR(m->types);
    return 0;
}

void box_method_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    box_method_clear(self);
    reinterpret_cast<freefunc>(PyType_GetSlot(type, Py_tp_free))(self);
    Py_DECREF(type);
}

// Only reached where the method is not called straight away, as in `f = box.update` or
// super().update: the interpreter calls a method descriptor without binding it first.
PyObject *box_method_get(PyObject *self, PyObject *box, PyObject *) noexcept {
    if (box == nullptr || box == Py_None)
        return Py_NewRef(self);
    PyObject *const call[] = {self, box};
    return PyObject_Vectorcall(method_type, call, 2, nullptr);
}

const char *method_name(const BoxMethod &m) { return m.kind == kind_update ? "update" : "snapshot"; }

PyObject *box_method_name(PyObject *self, void *) {
    return PyUnicode_InternFromString(method_name(*reinterpret_cast<BoxMethod *>(self)));
}

PyObject *box_method_qualname(PyObject *self, void *) {
    const BoxMethod &m = *reinterpret_cast<BoxMethod *>(self);
    return PyUnicode_FromFormat("%U.%s", m.qualname, method_name(m));
}

PyObject *box_method_doc(PyObject *self, void *) {
    return PyObject_GetAttrString(reinterpret_cast<BoxMethod *>(self)->fallback, "__doc__");
}

// inspect.signature follows __wrapped__ to the Python method, whose parameters this one takes.
PyObject *box_method_wrapped(PyObject *self, void *) {
    return Py_NewRef(reinterpret_cast<BoxMethod *>(self)->fallback);
}

// Pickled as getattr(cls, name), so the unpickled method is the one the class holds there.
PyObject *box_method_reduce(PyObject *self, PyObject *) {
    const BoxMethod &m = *reinterpret_cast<BoxMethod *>(self);
    return Py_BuildValue("O(Os)", getattr_function, m.cls, method_name(m));
}

PyGetSetDef box_method_getset[] = {{"__name__", box_method_name, nullptr, nullptr, nullptr},
                                   {"__qualname__", box_method_qualname, nullptr, nullptr, nullptr},
                                   {"__doc__", box_method_doc, nullptr, nullptr, nullptr},
                                   {"__wrapped__", box_method_wrapped, nullptr, nullptr, nullptr},
                                   {nullptr, nullptr, nullptr, nullptr, nullptr}};

PyMethodDef box_method_methods[] = {{"__reduce__", box_method_reduce, METH_NOARGS, nullptr},
                                    {nullptr, nullptr, 0, nullptr}};

PyMemberDef box_method_members[] = {
    {"__vectorcalloffset__", member_ssize_t, offsetof(BoxMethod, vectorcall), member_readonly, nullptr},
    {nullptr, 0, 0, 0, nullptr}};

PyType_Slot box_method_slots[] = {{Py_tp_new, reinterpret_cast<void *>(box_method_new)},
                                  {Py_tp_dealloc, reinterpret_cast<void *>(box_method_dealloc)},
                                  {Py_tp_traverse, reinterpret_cast<void *>(box_method_traverse)},
                                  {Py_tp_clear, reinterpret_cast<void *>(box_method_clear)},
                                  {Py_tp_call, reinterpret_cast<void *>(PyVectorcall_Call)},
                                  {Py_tp_descr_get, reinterpret_cast<void *>(box_method_get)},
                                  {Py_tp_members, box_method_members},
                                  {Py_tp_getset, box_method_getset},
                                  {Py_tp_methods, box_method_methods},
                                  {0, nullptr}};

PyType_Spec box_method_spec = {"sharedbox._native.BoxMethod", sizeof(BoxMethod), 0,
                               Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_HAVE_VECTORCALL |
                                   Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_METHOD_DESCRIPTOR,
                               box_method_slots};

// A consumer that takes the handle renames the capsule "used_sharedbox_box" and releases the
// handle itself; the struct's memory is freed here either way. PyCapsule_IsValid sets no error,
// so a destructor running while an exception is pending leaves it alone.
void release_box_capsule(PyObject *capsule) {
    const bool unused = PyCapsule_IsValid(capsule, "sharedbox_box") != 0;
    if (!unused && PyCapsule_IsValid(capsule, "used_sharedbox_box") == 0)
        return;
    auto *handle =
        static_cast<sbx_handle *>(PyCapsule_GetPointer(capsule, unused ? "sharedbox_box" : "used_sharedbox_box"));
    if (unused && handle->release != nullptr)
        handle->release(handle);
    delete handle;
}

int types_traverse(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self));
    if (!nb::inst_ready(self))
        return 0;
    return nb::inst_ptr<sharedbox::Types>(self)->traverse(visit, arg);
}

int types_clear(PyObject *self) {
    if (nb::inst_ready(self))
        nb::inst_ptr<sharedbox::Types>(self)->clear();
    return 0;
}

int value_cache_traverse(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self));
    if (!nb::inst_ready(self))
        return 0;
    return nb::inst_ptr<ValueCache>(self)->traverse(visit, arg);
}

int value_cache_clear(PyObject *self) {
    if (nb::inst_ready(self))
        nb::inst_ptr<ValueCache>(self)->clear();
    return 0;
}

PyType_Slot value_cache_slots[] = {{Py_tp_traverse, reinterpret_cast<void *>(value_cache_traverse)},
                                   {Py_tp_clear, reinterpret_cast<void *>(value_cache_clear)},
                                   {0, nullptr}};

template <typename Store> int store_traverse(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self));
    if (!nb::inst_ready(self))
        return 0;
    return nb::inst_ptr<Store>(self)->traverse(visit, arg);
}

template <typename Store> int store_clear(PyObject *self) {
    if (nb::inst_ready(self))
        nb::inst_ptr<Store>(self)->clear();
    return 0;
}

template <typename Store>
PyType_Slot store_slots[] = {{Py_tp_traverse, reinterpret_cast<void *>(store_traverse<Store>)},
                             {Py_tp_clear, reinterpret_cast<void *>(store_clear<Store>)},
                             {0, nullptr}};

/// The iterator of a NativeCollection, NativeSet or NativeDict: one element, or a dict's key, decoded per
/// step, forward up to the length the value has then, or backward from the end, as list's reversed iterator.
template <typename Store> struct StoreIterator {
    nb::object store;
    std::size_t index = 0;
    bool backward = false;
    /// A NativeSet's or NativeDict's length when the iteration began.
    std::size_t length = 0;
};

template <typename Store> int store_iterator_traverse(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self));
    if (!nb::inst_ready(self))
        return 0;
    Py_VISIT(nb::inst_ptr<StoreIterator<Store>>(self)->store.ptr());
    return 0;
}

template <typename Store> int store_iterator_clear(PyObject *self) {
    if (nb::inst_ready(self))
        nb::inst_ptr<StoreIterator<Store>>(self)->store.reset();
    return 0;
}

/// Sets the attributes object.__getstate__ gave on value, as unpickling sets them.
void restore(nb::handle value, nb::handle state) {
    nb::object attrs = nb::borrow(state);
    nb::object slots = nb::none();
    if (nb::isinstance<nb::tuple>(state)) {
        attrs = state[0];
        slots = state[1];
    }
    if (nb::bool_(attrs))
        value.attr("__dict__").attr("update")(attrs);
    if (!slots.is_none())
        for (auto [name, member] : nb::borrow<nb::dict>(slots))
            nb::setattr(value, name, member);
}

/// Adds __copy__, __deepcopy__ and __reduce__ to the class of Store. Each copies the stored form without
/// decoding an element and keeps a subclass's attributes, as copies and pickling of a list subclass keep them.
template <std::derived_from<sharedbox::SlotStore> Store> void def_copies(nb::class_<Store> &cls) {
    cls.def("__copy__",
            [](nb::pointer_and_handle<Store> self) {
                nb::object value = Store::copy(self);
                restore(value, self.h.attr("__getstate__")());
                return value;
            })
        .def("__deepcopy__",
             [](nb::pointer_and_handle<Store> self, nb::dict memo) {
                 nb::object value = Store::copy(self);
                 // Registered before the attributes are copied, so one that refers back to self gets value.
                 memo[nb::steal(PyLong_FromVoidPtr(self.h.ptr()))] = value;
                 restore(value,
                         nb::module_::import_("copy").attr("deepcopy")(self.h.attr("__getstate__")(), memo));
                 return value;
             })
        .def("__reduce__", [](nb::pointer_and_handle<Store> self) {
            nb::handle cls = reinterpret_cast<PyObject *>(Py_TYPE(self.h.ptr()));
            return nb::make_tuple(
                nb::module_::import_("sharedbox._bounded").attr("_rebuild"),
                nb::make_tuple(cls, self.p->element_annotation(), self.p->capacity(), self.p->stored()),
                self.h.attr("__getstate__")());
        });
}

// A slot rather than a bound __next__, so the end returns nullptr with no error set instead of throwing
// StopIteration through C++, whose unwinding costs microseconds per loop.
template <typename Store> PyObject *store_iterator_next(PyObject *self) {
    nb::ft_object_guard guard(self);
    try {
        StoreIterator<Store> &it = *nb::inst_ptr<StoreIterator<Store>>(self);
        if (!it.store.is_valid())
            return nullptr;
        if (it.backward && it.index == 0) {
            it.store.reset();
            return nullptr;
        }
        const auto *c = nb::inst_ptr<Store>(it.store);
        if constexpr (std::is_same_v<Store, sharedbox::NativeSet>) {
            if (c->size() != it.length)
                sharedbox::raise(PyExc_RuntimeError, "Set changed size during iteration");
        } else if constexpr (std::is_same_v<Store, sharedbox::NativeDict>) {
            if (c->size() != it.length)
                sharedbox::raise(PyExc_RuntimeError, "dictionary changed size during iteration");
        }
        nb::object value = c->element(static_cast<Py_ssize_t>(it.backward ? it.index - 1 : it.index));
        if (!value.is_valid()) {
            // An exhausted iterator drops the value so it stays exhausted, as list's does.
            it.store.reset();
            return nullptr;
        }
        it.backward ? --it.index : ++it.index;
        return value.release().ptr();
    } catch (...) {
        set_error();
        return nullptr;
    }
}

template <typename Store>
PyType_Slot store_iterator_slots[] = {{Py_tp_iternext, reinterpret_cast<void *>(store_iterator_next<Store>)},
                                      {Py_tp_traverse, reinterpret_cast<void *>(store_iterator_traverse<Store>)},
                                      {Py_tp_clear, reinterpret_cast<void *>(store_iterator_clear<Store>)},
                                      {0, nullptr}};

using CollectionIterator = StoreIterator<sharedbox::NativeCollection>;
using SetIterator = StoreIterator<sharedbox::NativeSet>;
using DictIterator = StoreIterator<sharedbox::NativeDict>;

// A slot rather than a bound __getitem__, which CPython would also install as sq_item: C code such as numpy
// would then take the value for a sequence, as it never takes a builtin dict, which fills only mp_subscript.
PyObject *dict_subscript(PyObject *self, PyObject *key) {
    try {
        if (!nb::inst_ready(self))
            sharedbox::raise(PyExc_TypeError, "the value was never built");
        return nb::inst_ptr<sharedbox::NativeDict>(self)->getitem(key).release().ptr();
    } catch (...) {
        set_error();
        return nullptr;
    }
}

// A slot for the same reason as dict_subscript: a bound __setitem__ or __delitem__ would also be installed as
// sq_ass_item. A null value means del.
int dict_ass_subscript(PyObject *self, PyObject *key, PyObject *value) {
    try {
        if (!nb::inst_ready(self))
            sharedbox::raise(PyExc_TypeError, "the value was never built");
        auto *d = nb::inst_ptr<sharedbox::NativeDict>(self);
        if (value == nullptr)
            d->delitem(key);
        else
            d->setitem(key, value);
        return 0;
    } catch (...) {
        set_error();
        return -1;
    }
}

int collection_getbuffer(PyObject *self, Py_buffer *view, int flags) {
    try {
        if (!nb::inst_ready(self))
            sharedbox::raise(PyExc_TypeError, "the value was never built");
        nb::inst_ptr<sharedbox::NativeCollection>(self)->export_buffer(self, view, flags);
        return 0;
    } catch (...) {
        view->obj = nullptr;
        set_error();
        return -1;
    }
}

void collection_releasebuffer(PyObject *self, Py_buffer *) {
    nb::inst_ptr<sharedbox::NativeCollection>(self)->release_buffer();
}

PyType_Slot collection_slots[] = {
    {Py_tp_traverse, reinterpret_cast<void *>(store_traverse<sharedbox::NativeCollection>)},
    {Py_tp_clear, reinterpret_cast<void *>(store_clear<sharedbox::NativeCollection>)},
    {Py_bf_getbuffer, reinterpret_cast<void *>(collection_getbuffer)},
    {Py_bf_releasebuffer, reinterpret_cast<void *>(collection_releasebuffer)},
    {0, nullptr}};

PyType_Slot dict_slots[] = {{Py_tp_traverse, reinterpret_cast<void *>(store_traverse<sharedbox::NativeDict>)},
                            {Py_tp_clear, reinterpret_cast<void *>(store_clear<sharedbox::NativeDict>)},
                            {Py_mp_subscript, reinterpret_cast<void *>(dict_subscript)},
                            {Py_mp_ass_subscript, reinterpret_cast<void *>(dict_ass_subscript)},
                            {0, nullptr}};

/// Raises TypeError when given, the number of arguments passed to name, passes most, as dict's methods do.
void at_most(const char *name, std::size_t most, std::size_t given) {
    if (given > most)
        sharedbox::raise(PyExc_TypeError, std::string(name) + " expected at most " + std::to_string(most) +
                                              (most == 1 ? " argument, got " : " arguments, got ") +
                                              std::to_string(given));
}

/// A view of self of the collections.abc class name: KeysView, ValuesView or ItemsView.
nb::object view(const char *name, nb::handle self) {
    return nb::module_::import_("collections.abc").attr(name)(self);
}

template <sharedbox::set_op op, bool reflected>
nb::object set_combine(nb::pointer_and_handle<sharedbox::NativeSet> self, nb::handle other) {
    return sharedbox::NativeSet::combine(self, other, op, reflected);
}

template <sharedbox::set_op op>
nb::object set_combine_in_place(nb::pointer_and_handle<sharedbox::NativeSet> self, nb::handle other) {
    return sharedbox::NativeSet::combine_in_place(self, other, op);
}

template <int op> nb::object set_compare(sharedbox::NativeSet &self, nb::handle other) {
    return self.compare(other, op);
}

PyType_Slot types_slots[] = {{Py_tp_traverse, reinterpret_cast<void *>(types_traverse)},
                             {Py_tp_clear, reinterpret_cast<void *>(types_clear)},
                             {0, nullptr}};

} // namespace

NB_MODULE(_native, m) {
    sharedbox::scalars::init();
    nb::exception<sharedbox::SegmentExists>(m, "SegmentExistsError", PyExc_FileExistsError);
    nb::exception<sharedbox::SegmentMissing>(m, "SegmentNotFoundError", PyExc_FileNotFoundError);
    nb::object schema_mismatch =
        nb::exception<sharedbox::SchemaMismatch>(m, "SchemaMismatchError", PyExc_TypeError);
    nb::exception<sharedbox::KindMismatch>(m, "KindMismatchError", schema_mismatch);
    nb::exception<sharedbox::SegmentClosed>(m, "BoxClosedError", PyExc_ValueError);
    nb::exception<sharedbox::LockTimeout>(m, "LockTimeoutError", PyExc_TimeoutError);
    nb::exception<sharedbox::NoWaiterSlot>(m, "WaiterSlotsFullError", PyExc_RuntimeError);
    nb::exception<sharedbox::MetadataFull>(m, "MetadataFullError", PyExc_RuntimeError);
    nb::exception<sharedbox::MetadataGone>(m, "MetadataGoneError", PyExc_LookupError);
    nb::register_exception_translator([](const std::exception_ptr &p, void *) {
        try {
            std::rethrow_exception(p);
        } catch (const std::system_error &e) {
            // Only errno values map onto OSError's errno; other categories fall through to RuntimeError.
            if (e.code().category() != std::generic_category())
                throw;
            nb::object error = nb::steal(PyObject_CallFunction(PyExc_OSError, "is", e.code().value(), e.what()));
            if (error.is_valid())
                PyErr_SetObject(PyExc_OSError, error.ptr());
        }
    });
    sharedbox::set_wait_hooks([]() -> void * { return PyEval_SaveThread(); },
                              [](void *state) { PyEval_RestoreThread(static_cast<PyThreadState *>(state)); });

    m.def(
        "check",
        [](std::uint32_t kind, std::uint32_t capacity, const std::string &name, nb::handle value) {
            if (kind > sharedbox::kind_ref)
                throw std::invalid_argument("unknown field kind " + std::to_string(kind));
            sharedbox::EncodeBuffer buffer;
            sharedbox::encode({0, capacity, to_kind(kind)}, name, value.ptr(), buffer);
        },
        "kind"_a, "capacity"_a, "name"_a, "value"_a.none());

    m.attr("LAYOUT_VERSION") = nb::make_tuple(sharedbox::layout_major, sharedbox::layout_minor);
    m.def("_process_start", [](std::uint32_t pid) { return sharedbox::detail::process_start(pid); }, "pid"_a);
    m.def(
        "_process_alive",
        [](std::uint32_t pid, std::uint64_t start) { return sharedbox::detail::process_alive(pid, start); },
        "pid"_a, "start"_a);

    nb::class_<sharedbox::FieldWriter>(m, "FieldWriter").def("end", &sharedbox::FieldWriter::end);

    nb::class_<Segment>(m, "Segment")
        .def_static(
            "create",
            [](const std::string &name,
               const std::vector<std::tuple<std::uint32_t, std::uint32_t, std::uint32_t>> &fields,
               const std::vector<std::string> &names, std::uint64_t record_size, std::uint64_t schema_hash,
               double lock_timeout, const Values &values, std::uint16_t waiter_slots, bool publish,
               nb::handle types, std::optional<nb::bytes> metadata, std::uint64_t metadata_capacity,
               std::uint32_t metadata_versions) {
                const sharedbox::Types *t = types_of(types);
                std::vector<sharedbox::FieldDesc> descs;
                descs.reserve(fields.size());
                for (const auto &[offset, capacity, kind] : fields)
                    descs.push_back({offset, capacity, to_kind(kind)});
                sharedbox::check_names(names, descs.size());
                Encoded encoded;
                encoded.reserve(values.size());
                for (const auto &[index, value] : values) {
                    sharedbox::check_index(index, descs.size());
                    sharedbox::EncodeBuffer buffer;
                    const std::span<const std::byte> bytes =
                        static_cast<std::uint8_t>(descs[index].kind) <= sharedbox::kind_ref
                            ? sharedbox::encode(descs[index], names[index], value.ptr(), buffer)
                            : sharedbox::need(t, index).encode(index, value.ptr(), buffer);
                    encoded.emplace_back(index,
                                         std::string(reinterpret_cast<const char *>(bytes.data()), bytes.size()));
                }
                return Segment::create(name, descs, names, record_size, schema_hash, lock_timeout, waiter_slots,
                                       encoded, t == nullptr ? std::string() : t->table(), publish,
                                       metadata ? std::string_view(metadata->c_str(), metadata->size())
                                                : std::string_view(),
                                       metadata_capacity, metadata_versions);
            },
            "name"_a, "fields"_a, "names"_a, "record_size"_a, "schema_hash"_a, "lock_timeout"_a, "values"_a,
            "waiter_slots"_a = sharedbox::default_waiter_slots, "publish"_a = true, "types"_a = nb::none(),
            "metadata"_a = nb::none(), "metadata_capacity"_a = 0, "metadata_versions"_a = 64)
        .def_static(
            "attach",
            [](const std::string &name, const std::vector<std::string> &names, std::uint64_t schema_hash,
               double lock_timeout, nb::handle types) {
                const sharedbox::Types *t = types_of(types);
                return Segment::attach(name, names, schema_hash, lock_timeout,
                                       t == nullptr ? nullptr : &t->table());
            },
            "name"_a, "names"_a, "schema_hash"_a, "lock_timeout"_a, "types"_a = nb::none())
        .def(
            "get",
            [](const Segment &s, std::uint32_t index, nb::handle types) { return get(s, index, types_of(types)); },
            "field"_a, "types"_a = nb::none())
        .def(
            "read_into",
            [](const Segment &s, std::uint32_t index, nb::handle out, nb::handle types) {
                const auto view = sharedbox::need(types_of(types), index).array_out(index, out.ptr());
                s.read_into(index, {static_cast<std::byte *>(view.data()), view.nbytes()});
            },
            "field"_a, "out"_a, "types"_a = nb::none())
        .def(
            "begin_write",
            [](const Segment &s, std::uint32_t index, nb::handle types) {
                const sharedbox::Types &t = sharedbox::need(types_of(types), index);
                if (t.field(index).kind != sharedbox::kind_array)
                    throw nb::type_error((t.label(index) + " is not an array field").c_str());
                std::unique_ptr<sharedbox::FieldWriter> writer = s.begin_write(index);
                auto keep = std::make_unique<std::shared_ptr<sharedbox::handle>>(writer->box());
                nb::capsule owner(keep.get(), [](void *p) noexcept {
                    delete static_cast<std::shared_ptr<sharedbox::handle> *>(p);
                });
                // Released only once the capsule exists, so a failure to make it does not leak the copy.
                keep.release();
                nb::object view = t.array_over(index, writer->bytes().data(), owner);
                return nb::make_tuple(nb::cast(std::move(writer)), view);
            },
            "field"_a, "types"_a = nb::none())
        .def(
            "get_versioned",
            [](const Segment &s, std::uint32_t index, nb::handle types) {
                sharedbox::FieldRead read;
                s.read(index, read);
                const std::uint64_t version = read.version;
                return nb::make_tuple(version, decode_read(types_of(types), index, read));
            },
            "field"_a, "types"_a = nb::none())
        .def(
            "read_versioned",
            [](const Segment &s, std::uint32_t index) {
                sharedbox::FieldRead read;
                s.read(index, read);
                return nb::make_tuple(read.version, nb::bytes(read.bytes.data(), read.bytes.size()));
            },
            "field"_a)
        .def_prop_ro("layout_version",
                     [](const Segment &s) { return nb::make_tuple(s.major_version(), s.minor_version()); })
        .def(
            "cached_ref",
            [](const Segment &s, std::uint32_t index, nb::dict cache) -> nb::object {
                const std::uint64_t create_id = stored_ref_id(s, index);
                if (create_id == 0)
                    return nb::none();
                const nb::object key = nb::int_(index);
                const nb::object entry = nb::steal(PyObject_GetItem(cache.ptr(), key.ptr()));
                if (!entry.is_valid()) {
                    if (!PyErr_ExceptionMatches(PyExc_KeyError))
                        throw nb::python_error();
                    PyErr_Clear();
                    return nb::bool_(false);
                }
                // The entry shape is RefEntry in _box.py: (create_id, box, the box's Segment).
                const auto bad_entry = [] {
                    return nb::type_error("a reference cache entry is not (create_id, box, Segment)");
                };
                if (!PyTuple_Check(entry.ptr()) || PyTuple_Size(entry.ptr()) != 3)
                    throw bad_entry();
                PyObject *id = PyTuple_GetItem(entry.ptr(), 0);
                const nb::handle segment = PyTuple_GetItem(entry.ptr(), 2);
                if (!PyLong_Check(id) || PyBool_Check(id) || !nb::isinstance<Segment>(segment))
                    throw bad_entry();
                const unsigned long long cached_id = PyLong_AsUnsignedLongLong(id);
                if (cached_id == static_cast<unsigned long long>(-1) && PyErr_Occurred()) {
                    PyErr_Clear();
                    throw bad_entry();
                }
                if (cached_id != create_id || nb::cast<const Segment &>(segment).closed())
                    return nb::bool_(false);
                return nb::borrow(PyTuple_GetItem(entry.ptr(), 1));
            },
            "field"_a, "cache"_a)
        .def(
            "get_dict",
            [](const Segment &s, nb::tuple names, nb::handle types) -> nb::dict {
                if (names.size() != s.field_count())
                    throw std::invalid_argument("got " + std::to_string(names.size()) + " field names for " +
                                                std::to_string(s.field_count()) + " fields");
                const sharedbox::Types *t = types_of(types);
                return sharedbox::with_record(s, [&](const std::byte *record) {
                    nb::dict out;
                    for (std::uint32_t i = 0; i < s.field_count(); ++i)
                        out[names[i]] = decode_payload(t, s.fields()[i], i, record);
                    return out;
                });
            },
            "names"_a, "types"_a = nb::none())
        .def(
            "set",
            [](Segment &s, const Values &values, nb::handle types, std::optional<nb::bytes> doc) {
                Scratch<sharedbox::Pending, 8> pending(values.size());
                for (std::size_t i = 0; i < values.size(); ++i)
                    pending[i] = {values[i].first, values[i].second.ptr()};
                Scratch<sharedbox::EncodeBuffer, 8> buffers(values.size());
                Scratch<sharedbox::value, 8> encoded(values.size());
                sharedbox::encode_all(s, types_of(types), pending.span(), buffers.span(), encoded.span());
                if (doc)
                    s.write(encoded.span(), std::string_view(doc->c_str(), doc->size()));
                else
                    s.write(encoded.span());
            },
            "values"_a, "types"_a = nb::none(), "doc"_a = nb::none())
        .def(
            "_read",
            [](const Segment &s, std::uint32_t index) {
                sharedbox::FieldRead read;
                s.read(index, read);
                return nb::bytes(read.bytes.data(), read.bytes.size());
            },
            "field"_a)
        .def("_read_all",
             [](const Segment &s) -> nb::typed<nb::list, nb::bytes> {
                 return sharedbox::with_record(s, [&](const std::byte *record) {
                     nb::list_builder out(s.field_count());
                     for (std::uint32_t i = 0; i < s.field_count(); ++i) {
                         const std::string_view value = sharedbox::payload(s.fields()[i], record);
                         out.put(nb::bytes(value.data(), value.size()));
                     }
                     return out.commit();
                 });
             })
        .def(
            "_write",
            [](Segment &s, const std::vector<std::pair<std::uint32_t, nb::bytes>> &values) {
                std::vector<sharedbox::value> converted;
                converted.reserve(values.size());
                for (const auto &[index, data] : values) {
                    // Checked before the narrowing cast, which would otherwise turn index 65536 into field 0.
                    sharedbox::check_index(index, s.field_count());
                    converted.push_back(
                        {static_cast<std::uint16_t>(index), std::as_bytes(std::span(data.c_str(), data.size()))});
                }
                s.write(converted);
            },
            "values"_a)
        .def("version", &Segment::version, "field"_a)
        .def("versions", &Segment::versions)
        .def("metadata_head", &Segment::metadata_head)
        .def("metadata_length", &Segment::metadata_length, "version"_a)
        .def(
            "read_metadata",
            [](const Segment &s, std::uint64_t version) {
                const std::string doc = s.read_metadata(version);
                return nb::bytes(doc.data(), doc.size());
            },
            "version"_a)
        .def("publish", &Segment::publish)
        .def("generation", &Segment::generation)
        .def("register_waiter", &Segment::register_waiter)
        .def("release_waiter", &Segment::release_waiter, "slot"_a)
        .def("waiter_held", &Segment::waiter_held, "slot"_a)
        .def("interrupt", &Segment::interrupt, "slot"_a)
        .def("_export",
             [](const Segment &s) {
                 sbx_handle *handle = s.export_handle();
                 PyObject *capsule = PyCapsule_New(handle, "sharedbox_box", release_box_capsule);
                 if (capsule == nullptr) {
                     handle->release(handle);
                     delete handle;
                     throw nb::python_error();
                 }
                 return nb::steal(capsule);
             })
        .def("wait", &Segment::wait, "last_generation"_a, "timeout"_a, "slot"_a = nb::none(),
             nb::call_guard<nb::gil_scoped_release>())
        .def("force_unlock", &Segment::force_unlock)
        .def("_hold_write_lock", &Segment::hold_write_lock)
        .def("_release_held_lock", &Segment::release_held_lock)
        .def("_after_fork", &Segment::after_fork)
        // A read blocked on another writer's lock releases the GIL and needs it back before it
        // lets close() through, so close() must not hold the GIL while it waits.
        .def("close", &Segment::close, nb::call_guard<nb::gil_scoped_release>())
        .def_static("unlink", &Segment::unlink, "name"_a)
        .def_prop_ro("_size", &Segment::size)
        .def_prop_ro("_waiters", &Segment::waiters)
        .def_prop_ro("create_id", &Segment::create_id)
        .def_prop_ro("closed", &Segment::closed)
        .def_prop_ro("published", &Segment::published)
        .def_prop_ro("name", &Segment::name)
        .def_prop_ro("lock_timeout", &Segment::lock_timeout);

    segment_type = reinterpret_cast<PyTypeObject *>(nb::type<Segment>().ptr());
    reraise = nb::cpp_function([] { std::rethrow_exception(std::exchange(pending, nullptr)); }).release().ptr();

    nb::class_<Field>(m, "Field", nb::type_slots(field_slots))
        .def(
            "__init__",
            [](Field *self, nb::object spec, nb::object segment_slot, nb::object types, nb::object values_slot) {
                auto read_slot =
                    reinterpret_cast<descrgetfunc>(PyType_GetSlot(Py_TYPE(segment_slot.ptr()), Py_tp_descr_get));
                if (read_slot == nullptr)
                    throw nb::type_error("segment_slot must be a descriptor");
                const auto index = nb::cast<std::uint32_t>(spec.attr("index"));
                const sharedbox::Types *t = types_of(types);
                const bool reusable = t != nullptr && !values_slot.is_none() && t->field_reusable(index);
                descrgetfunc read_values = nullptr;
                if (reusable) {
                    read_values = reinterpret_cast<descrgetfunc>(
                        PyType_GetSlot(Py_TYPE(values_slot.ptr()), Py_tp_descr_get));
                    if (read_values == nullptr)
                        throw nb::type_error("values_slot must be a descriptor");
                }
                new (self) Field{std::move(spec),  index, std::move(segment_slot), read_slot,
                                 std::move(types), t,     std::move(values_slot),  read_values,
                                 reusable};
            },
            "spec"_a, "segment_slot"_a, "types"_a = nb::none(), "values_slot"_a = nb::none())
        .def_ro("spec", &Field::spec);

    nb::class_<ValueCache>(m, "ValueCache", nb::type_slots(value_cache_slots))
        .def(nb::init<std::uint32_t>(), "fields"_a)
        .def("clear", &ValueCache::clear)
        .def("_after_fork", &ValueCache::after_fork);
    value_cache_type = reinterpret_cast<PyTypeObject *>(nb::type<ValueCache>().ptr());

    nb::class_<sharedbox::Types>(m, "Types", nb::type_slots(types_slots))
        .def(
            "__init__",
            [](sharedbox::Types *self,
               const std::vector<std::tuple<std::uint32_t, std::uint32_t, std::uint32_t>> &fields,
               std::vector<std::string> labels, nb::bytes table, const nb::dict &info,
               const std::vector<std::pair<std::int64_t, std::uint32_t>> &bytearrays,
               const std::vector<std::uint32_t> &reusable, const std::vector<sharedbox::Convert> &converts,
               bool stream) {
                new (self) sharedbox::Types(fields, std::move(labels), std::string(table.c_str(), table.size()),
                                            info, bytearrays, reusable, converts, stream);
            },
            "fields"_a, "labels"_a, "table"_a, "info"_a, "bytearrays"_a,
            "reusable"_a = std::vector<std::uint32_t>(), "converts"_a = std::vector<sharedbox::Convert>(),
            "stream"_a = false)
        .def(
            "check",
            [](const sharedbox::Types &t, std::uint32_t index, nb::handle value) {
                sharedbox::EncodeBuffer buffer;
                static_cast<void>(t.encode(index, value.ptr(), buffer));
            },
            "field"_a, "value"_a.none())
        .def(
            "decode",
            [](const sharedbox::Types &t, std::uint32_t index, nb::bytes data) {
                return t.decode(index, std::as_bytes(std::span(data.c_str(), data.size())), nullptr);
            },
            "field"_a, "data"_a);

    nb::class_<CollectionIterator>(m, "NativeCollectionIterator", nb::is_final(),
                                   nb::type_slots(store_iterator_slots<sharedbox::NativeCollection>))
        .def("__iter__", [](nb::handle self) { return nb::borrow(self); });
    nb::class_<SetIterator>(m, "NativeSetIterator", nb::is_final(),
                            nb::type_slots(store_iterator_slots<sharedbox::NativeSet>))
        .def("__iter__", [](nb::handle self) { return nb::borrow(self); });
    nb::class_<DictIterator>(m, "NativeDictIterator", nb::is_final(),
                             nb::type_slots(store_iterator_slots<sharedbox::NativeDict>))
        .def("__iter__", [](nb::handle self) { return nb::borrow(self); });

    nb::class_<sharedbox::NativeCollection> collection(m, "NativeCollection", nb::is_generic(),
                                                       nb::type_slots(collection_slots));
    collection.def("__len__", &sharedbox::NativeCollection::size)
        .def("__getitem__", &sharedbox::NativeCollection::getitem)
        .def("__setitem__", &sharedbox::NativeCollection::setitem, nb::arg(), nb::arg().none())
        .def("__delitem__", &sharedbox::NativeCollection::delitem)
        .def("insert", &sharedbox::NativeCollection::insert, nb::arg(), nb::arg().none())
        .def("append", &sharedbox::NativeCollection::append, nb::arg().none())
        .def("extend", &sharedbox::NativeCollection::extend)
        .def("__iadd__",
             [](nb::pointer_and_handle<sharedbox::NativeCollection> self, nb::handle values) {
                 self.p->extend(values);
                 return nb::borrow(self.h);
             })
        .def("pop", &sharedbox::NativeCollection::pop, nb::arg() = -1)
        .def("remove", &sharedbox::NativeCollection::remove, nb::arg().none())
        .def("index", &sharedbox::NativeCollection::index, nb::arg().none(), nb::arg() = 0,
             nb::arg() = PY_SSIZE_T_MAX)
        .def("count", &sharedbox::NativeCollection::count, nb::arg().none())
        .def("__contains__", &sharedbox::NativeCollection::contains, nb::arg().none())
        .def("reverse", &sharedbox::NativeCollection::reverse)
        .def("clear", &sharedbox::NativeCollection::remove_all)
        .def("__eq__", &sharedbox::NativeCollection::equals, nb::arg().none())
        .def("__iter__",
             [](nb::pointer_and_handle<sharedbox::NativeCollection> self) {
                 return CollectionIterator{nb::borrow(self.h)};
             })
        .def("__reversed__",
             [](nb::pointer_and_handle<sharedbox::NativeCollection> self) {
                 return CollectionIterator{nb::borrow(self.h), self.p->size(), true};
             })
        .def("copy", &sharedbox::NativeCollection::copy)
        .def("__add__", &sharedbox::NativeCollection::add, nb::arg().none())
        .def("__mul__", &sharedbox::NativeCollection::multiply, nb::arg().none())
        .def("__rmul__", &sharedbox::NativeCollection::multiply, nb::arg().none())
        .def("__imul__",
             [](nb::pointer_and_handle<sharedbox::NativeCollection> self, nb::handle times) {
                 self.p->repeat(times);
                 return nb::borrow(self.h);
             })
        .def_prop_ro("_element_annotation", &sharedbox::NativeCollection::element_annotation)
        .def_prop_ro("capacity", &sharedbox::NativeCollection::capacity)
        .def("_stored", &sharedbox::NativeCollection::stored);
    // Equality compares elements that can change, so a value has no hash, as a list has none.
    collection.attr("__hash__") = nb::none();
    nb::module_::import_("collections.abc").attr("MutableSequence").attr("register")(collection);

    nb::class_<sharedbox::NativeSet> set(m, "NativeSet", nb::is_generic(),
                                         nb::type_slots(store_slots<sharedbox::NativeSet>));
    set.def("__len__", &sharedbox::NativeSet::size)
        .def("__iter__",
             [](nb::pointer_and_handle<sharedbox::NativeSet> self) {
                 return SetIterator{nb::borrow(self.h), 0, false, self.p->size()};
             })
        .def("__contains__", &sharedbox::NativeSet::contains, nb::arg().none())
        .def("add", &sharedbox::NativeSet::add, nb::arg().none())
        .def("discard", &sharedbox::NativeSet::discard, nb::arg().none())
        .def("remove", &sharedbox::NativeSet::remove, nb::arg().none())
        .def("pop", &sharedbox::NativeSet::pop)
        .def("clear", &sharedbox::NativeSet::remove_all)
        .def("update", &sharedbox::NativeSet::update)
        .def("__eq__", &sharedbox::NativeSet::equals, nb::arg().none())
        .def("copy", &sharedbox::NativeSet::copy)
        .def("__or__", &set_combine<sharedbox::set_op::join, false>, nb::arg().none())
        .def("__and__", &set_combine<sharedbox::set_op::common, false>, nb::arg().none())
        .def("__sub__", &set_combine<sharedbox::set_op::difference, false>, nb::arg().none())
        .def("__xor__", &set_combine<sharedbox::set_op::symmetric, false>, nb::arg().none())
        .def("__ror__", &set_combine<sharedbox::set_op::join, true>, nb::arg().none())
        .def("__rand__", &set_combine<sharedbox::set_op::common, true>, nb::arg().none())
        .def("__rsub__", &set_combine<sharedbox::set_op::difference, true>, nb::arg().none())
        .def("__rxor__", &set_combine<sharedbox::set_op::symmetric, true>, nb::arg().none())
        .def("__ior__", &set_combine_in_place<sharedbox::set_op::join>, nb::arg().none())
        .def("__iand__", &set_combine_in_place<sharedbox::set_op::common>, nb::arg().none())
        .def("__isub__", &set_combine_in_place<sharedbox::set_op::difference>, nb::arg().none())
        .def("__ixor__", &set_combine_in_place<sharedbox::set_op::symmetric>, nb::arg().none())
        .def("__le__", &set_compare<Py_LE>, nb::arg().none())
        .def("__lt__", &set_compare<Py_LT>, nb::arg().none())
        .def("__ge__", &set_compare<Py_GE>, nb::arg().none())
        .def("__gt__", &set_compare<Py_GT>, nb::arg().none())
        .def("isdisjoint", &sharedbox::NativeSet::isdisjoint)
        .def_prop_ro("_element_annotation", &sharedbox::NativeSet::element_annotation)
        .def_prop_ro("capacity", &sharedbox::NativeSet::capacity)
        .def("_stored", &sharedbox::NativeSet::stored);
    // A set's elements can change, so a value has no hash, as a set has none.
    set.attr("__hash__") = nb::none();
    nb::module_::import_("collections.abc").attr("MutableSet").attr("register")(set);

    nb::class_<sharedbox::NativeDict> dict(m, "NativeDict", nb::is_generic(), nb::type_slots(dict_slots));
    dict.def("__len__", &sharedbox::NativeDict::size)
        .def("__iter__",
             [](nb::pointer_and_handle<sharedbox::NativeDict> self) {
                 return DictIterator{nb::borrow(self.h), 0, false, self.p->size()};
             })
        .def("__reversed__",
             [](nb::pointer_and_handle<sharedbox::NativeDict> self) {
                 const std::size_t size = self.p->size();
                 return DictIterator{nb::borrow(self.h), size, true, size};
             })
        .def("get", &sharedbox::NativeDict::get, nb::arg().none(), nb::arg().none() = nb::none())
        .def("__contains__", &sharedbox::NativeDict::contains, nb::arg().none())
        .def("__eq__", &sharedbox::NativeDict::equals, nb::arg().none())
        .def(
            "pop",
            // nb::args, since a default that is not given must differ from every value, None included.
            [](sharedbox::NativeDict &d, nb::handle key, nb::args rest) {
                at_most("pop", 2, rest.size() + 1);
                return d.pop(key, rest.size() != 0 ? nb::handle(PyTuple_GetItem(rest.ptr(), 0)) : nb::handle());
            },
            nb::arg().none(), nb::arg("args"))
        .def("popitem", &sharedbox::NativeDict::popitem)
        .def("setdefault", &sharedbox::NativeDict::setdefault, nb::arg().none(), nb::arg().none() = nb::none())
        .def("update",
             [](sharedbox::NativeDict &d, nb::args others, nb::kwargs pairs) {
                 at_most("update", 1, others.size());
                 d.update(others.size() != 0 ? nb::handle(PyTuple_GetItem(others.ptr(), 0)) : nb::handle(), pairs);
             })
        .def("clear", &sharedbox::NativeDict::remove_all)
        .def(
            "__or__",
            [](nb::pointer_and_handle<sharedbox::NativeDict> self, nb::handle other) {
                return sharedbox::NativeDict::merge(self, other, false);
            },
            nb::arg().none())
        .def(
            "__ror__",
            [](nb::pointer_and_handle<sharedbox::NativeDict> self, nb::handle other) {
                return sharedbox::NativeDict::merge(self, other, true);
            },
            nb::arg().none())
        .def(
            "__ior__",
            // Any argument update takes, as dict's |= takes it.
            [](nb::pointer_and_handle<sharedbox::NativeDict> self, nb::handle other) {
                self.p->update(other, nb::kwargs());
                return nb::borrow(self.h);
            },
            nb::arg().none())
        .def("copy", &sharedbox::NativeDict::copy)
        .def("keys", [](nb::handle self) { return view("KeysView", self); })
        .def("values", [](nb::handle self) { return view("ValuesView", self); })
        .def("items", [](nb::handle self) { return view("ItemsView", self); })
        .def_prop_ro("_element_annotation", &sharedbox::NativeDict::element_annotation)
        .def_prop_ro("capacity", &sharedbox::NativeDict::capacity)
        .def("_stored", &sharedbox::NativeDict::stored);
    // A dict's items can change, so a value has no hash, as a dict has none.
    dict.attr("__hash__") = nb::none();
    nb::module_::import_("collections.abc").attr("MutableMapping").attr("register")(dict);
    def_copies(collection);
    def_copies(set);
    def_copies(dict);

    method_type = nb::object(nb::module_::import_("types").attr("MethodType")).release().ptr();
    getattr_function = nb::object(nb::module_::import_("builtins").attr("getattr")).release().ptr();
    nb::object box_method = nb::steal(PyType_FromSpec(&box_method_spec));
    if (!box_method.is_valid())
        throw nb::python_error();
    m.attr("BoxMethod") = box_method;

    sharedbox::bind_stream(m);
}
