#include <nanobind/nanobind.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>

#include <cstddef>
#include <cstring>
#include <exception>
#include <iterator>
#include <memory>
#include <new>
#include <span>
#include <string_view>
#include <system_error>
#include <type_traits>
#include <utility>

#include <sharedbox/sharedbox.hpp>

#if PY_VERSION_HEX < 0x030C0000
#include <structmember.h>
#endif

#include "codec.hpp"
#include "scalars.hpp"
#include "segment.hpp"
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
        if (read.field->kind == sharedbox::FieldKind::Bytes && types != nullptr &&
            types->field_is_bytearray(index)) [[unlikely]]
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
        if (field.kind == sharedbox::FieldKind::Bytes && types != nullptr && types->field_is_bytearray(index))
            [[unlikely]]
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
        return get(*nb::inst_ptr<Segment>(segment), f.index, f.t).release().ptr();
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

PyType_Slot field_slots[] = {{Py_tp_descr_get, reinterpret_cast<void *>(field_get)},
                             {Py_tp_descr_set, reinterpret_cast<void *>(field_set)},
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

// Holds size value-initialised entries: up to N in place, more on the heap. Only the entries in use are
// constructed, since MSVC constructs and destroys a whole array of a class type through a call per
// element, which costs more than the rest of a small update.
template <typename T, std::size_t N> class Scratch {
    // The constructor builds the entries outside any cleanup that would free the heap block.
    static_assert(std::is_nothrow_default_constructible_v<T>);

public:
    explicit Scratch(std::size_t size)
        : size_(size), data_(size <= N ? reinterpret_cast<T *>(local_) : std::allocator<T>().allocate(size)) {
        std::uninitialized_value_construct_n(data_, size_);
    }
    Scratch(const Scratch &) = delete;
    Scratch &operator=(const Scratch &) = delete;
    ~Scratch() {
        std::destroy_n(data_, size_);
        if (size_ > N)
            std::allocator<T>().deallocate(data_, size_);
    }
    T &operator[](std::size_t i) { return data_[i]; }
    std::span<T> span() { return {data_, size_}; }

private:
    alignas(T) std::byte local_[N * sizeof(T)];
    std::size_t size_;
    T *data_;
};

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

// Raises the TypeError of SharedBox._check_names for every name in kwnames that is not a field.
void raise_unknown(const BoxMethod &m, PyObject *kwnames) {
    nb::list unknown;
    for (Py_ssize_t k = 0; k < tuple_size(kwnames); ++k)
        if (field_index(m, tuple_item(kwnames, k)) < 0)
            unknown.append(nb::handle(tuple_item(kwnames, k)));
    unknown.sort();
    const nb::object joined = nb::str(", ").attr("join")(unknown);
    PyErr_Format(PyExc_TypeError, "%U has no field(s) %U", m.qualname, joined.ptr());
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
            if (index < 0) {
                raise_unknown(m, kwnames);
                return nullptr;
            }
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
    Py_CLEAR(m->cls);
    Py_CLEAR(m->qualname);
    Py_CLEAR(m->names);
    Py_CLEAR(m->specs);
    Py_CLEAR(m->helper);
    Py_CLEAR(m->follow);
    Py_CLEAR(m->fallback);
    Py_CLEAR(m->segment_slot);
    Py_CLEAR(m->types);
    m->t = nullptr;
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

PyType_Slot types_slots[] = {{Py_tp_traverse, reinterpret_cast<void *>(types_traverse)},
                             {Py_tp_clear, reinterpret_cast<void *>(types_clear)},
                             {0, nullptr}};

} // namespace

NB_MODULE(_native, m) {
    sharedbox::scalars::init();
    nb::exception<sharedbox::SegmentExists>(m, "SegmentExistsError", PyExc_FileExistsError);
    nb::exception<sharedbox::SegmentMissing>(m, "SegmentNotFoundError", PyExc_FileNotFoundError);
    nb::exception<sharedbox::SchemaMismatch>(m, "SchemaMismatchError", PyExc_TypeError);
    nb::exception<sharedbox::SegmentClosed>(m, "BoxClosedError", PyExc_ValueError);
    nb::exception<sharedbox::LockTimeout>(m, "LockTimeoutError", PyExc_TimeoutError);
    nb::exception<sharedbox::NoWaiterSlot>(m, "WaiterSlotsFullError", PyExc_RuntimeError);
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

    nb::class_<Segment>(m, "Segment")
        .def_static(
            "create",
            [](const std::string &name,
               const std::vector<std::tuple<std::uint32_t, std::uint32_t, std::uint32_t>> &fields,
               const std::vector<std::string> &names, std::uint64_t record_size, std::uint64_t schema_hash,
               double lock_timeout, const Values &values, std::uint16_t waiter_slots, bool publish,
               nb::handle types) {
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
                                       encoded, t == nullptr ? std::string() : t->table(), sharedbox::layout_major,
                                       publish);
            },
            "name"_a, "fields"_a, "names"_a, "record_size"_a, "schema_hash"_a, "lock_timeout"_a, "values"_a,
            "waiter_slots"_a = sharedbox::default_waiter_slots, "publish"_a = true, "types"_a = nb::none())
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
        .def_static(
            "_create_layout_1",
            [](const std::string &name,
               const std::vector<std::tuple<std::uint32_t, std::uint32_t, std::uint32_t>> &fields,
               const std::vector<std::string> &names, std::uint64_t record_size, std::uint64_t schema_hash,
               double lock_timeout, const Values &values) {
                std::vector<sharedbox::FieldDesc> descs;
                for (const auto &[offset, capacity, kind] : fields) {
                    if (kind > sharedbox::kind_ref)
                        throw std::invalid_argument("a layout 1.0 box holds kinds 0 to 5 only");
                    descs.push_back({offset, capacity, static_cast<sharedbox::FieldKind>(kind)});
                }
                sharedbox::check_names(names, descs.size());
                Encoded encoded;
                for (const auto &[index, value] : values) {
                    sharedbox::check_index(index, descs.size());
                    sharedbox::EncodeBuffer buffer;
                    const auto bytes = sharedbox::encode(descs[index], names[index], value.ptr(), buffer);
                    encoded.emplace_back(index,
                                         std::string(reinterpret_cast<const char *>(bytes.data()), bytes.size()));
                }
                return Segment::create(name, descs, names, record_size, schema_hash, lock_timeout,
                                       sharedbox::default_waiter_slots, encoded, std::string(), 1, true);
            },
            "name"_a, "fields"_a, "names"_a, "record_size"_a, "schema_hash"_a, "lock_timeout"_a, "values"_a)
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
            [](Segment &s, const Values &values, nb::handle types) {
                Scratch<sharedbox::Pending, 8> pending(values.size());
                for (std::size_t i = 0; i < values.size(); ++i)
                    pending[i] = {values[i].first, values[i].second.ptr()};
                Scratch<sharedbox::EncodeBuffer, 8> buffers(values.size());
                Scratch<sharedbox::value, 8> encoded(values.size());
                sharedbox::encode_all(s, types_of(types), pending.span(), buffers.span(), encoded.span());
                s.write(encoded.span());
            },
            "values"_a, "types"_a = nb::none())
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
            [](Field *self, nb::object spec, nb::object segment_slot, nb::object types) {
                auto read_slot =
                    reinterpret_cast<descrgetfunc>(PyType_GetSlot(Py_TYPE(segment_slot.ptr()), Py_tp_descr_get));
                if (read_slot == nullptr)
                    throw nb::type_error("segment_slot must be a descriptor");
                const auto index = nb::cast<std::uint32_t>(spec.attr("index"));
                const sharedbox::Types *t = types_of(types);
                new (self) Field{std::move(spec), index, std::move(segment_slot), read_slot, std::move(types), t};
            },
            "spec"_a, "segment_slot"_a, "types"_a = nb::none())
        .def_ro("spec", &Field::spec);

    nb::class_<sharedbox::Types>(m, "Types", nb::type_slots(types_slots))
        .def(
            "__init__",
            [](sharedbox::Types *self,
               const std::vector<std::tuple<std::uint32_t, std::uint32_t, std::uint32_t>> &fields,
               std::vector<std::string> labels, nb::bytes table, const nb::dict &info,
               const std::vector<std::pair<std::int64_t, std::uint32_t>> &bytearrays) {
                new (self) sharedbox::Types(fields, std::move(labels), std::string(table.c_str(), table.size()),
                                            info, bytearrays);
            },
            "fields"_a, "labels"_a, "table"_a, "info"_a, "bytearrays"_a)
        .def_prop_ro("table",
                     [](const sharedbox::Types &t) { return nb::bytes(t.table().data(), t.table().size()); })
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

    method_type = nb::object(nb::module_::import_("types").attr("MethodType")).release().ptr();
    getattr_function = nb::object(nb::module_::import_("builtins").attr("getattr")).release().ptr();
    nb::object box_method = nb::steal(PyType_FromSpec(&box_method_spec));
    if (!box_method.is_valid())
        throw nb::python_error();
    m.attr("BoxMethod") = box_method;
}
