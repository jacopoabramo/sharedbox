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
#include <utility>

#include <sharedbox/sharedbox.hpp>

#include "codec.hpp"
#include "segment.hpp"

namespace nb = nanobind;
using namespace nb::literals;
using sharedbox::Segment;

namespace {

using Values = std::vector<std::pair<std::uint32_t, nb::object>>;
using Encoded = std::vector<std::pair<std::uint32_t, std::string>>;

sharedbox::FieldKind to_kind(std::uint32_t code) {
    if (!sharedbox::kind_is_valid(code))
        throw std::invalid_argument("unknown field kind " + std::to_string(code));
    return static_cast<sharedbox::FieldKind>(code);
}

nb::object decode_value(const sharedbox::FieldDesc &field, std::string_view bytes) {
    PyObject *value = sharedbox::decode(field, bytes.data(), bytes.size());
    if (value == nullptr)
        throw nb::python_error();
    return nb::steal(value);
}

nb::object get(const Segment &s, std::uint32_t index) {
    sharedbox::FieldRead read;
    s.read(index, read);
    return decode_value(*read.field, read.bytes);
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

void set_one(Segment &s, std::uint32_t index, nb::handle value) {
    s.write_one(index, sharedbox::encode(s.field(index), s.field_name(index), value.ptr()));
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
};

// A new reference to the Segment in box's _segment slot, or nullptr with an exception set: the
// AttributeError of an empty slot, as a Python descriptor reading box._segment would raise. A Segment
// made by Segment.__new__ alone has no C++ object behind it, so it counts as the wrong type.
PyObject *segment_of(const Field &f, PyObject *box) {
    PyObject *segment = f.read_slot(f.segment_slot.ptr(), box, reinterpret_cast<PyObject *>(Py_TYPE(box)));
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
    const nb::object segment = nb::steal(segment_of(f, box));
    if (!segment.is_valid())
        return nullptr;
    try {
        return get(*nb::inst_ptr<Segment>(segment), f.index).release().ptr();
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
    const nb::object segment = nb::steal(segment_of(f, box));
    if (!segment.is_valid())
        return -1;
    try {
        set_one(*nb::inst_ptr<Segment>(segment), f.index, value);
        return 0;
    } catch (...) {
        set_error();
        return -1;
    }
}

PyType_Slot field_slots[] = {{Py_tp_descr_get, reinterpret_cast<void *>(field_get)},
                             {Py_tp_descr_set, reinterpret_cast<void *>(field_set)},
                             {0, nullptr}};

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

} // namespace

NB_MODULE(_native, m) {
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
            sharedbox::encode({0, capacity, to_kind(kind)}, name, value.ptr());
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
               double lock_timeout, const Values &values, std::uint16_t waiter_slots, bool publish) {
                std::vector<sharedbox::FieldDesc> descs;
                descs.reserve(fields.size());
                for (const auto &[offset, capacity, kind] : fields)
                    descs.push_back({offset, capacity, to_kind(kind)});
                sharedbox::check_names(names, descs.size());
                Encoded encoded;
                encoded.reserve(values.size());
                for (const auto &[index, value] : values) {
                    sharedbox::check_index(index, descs.size());
                    encoded.emplace_back(index, sharedbox::encode(descs[index], names[index], value.ptr()));
                }
                return Segment::create(name, descs, names, record_size, schema_hash, lock_timeout, waiter_slots,
                                       encoded, publish);
            },
            "name"_a, "fields"_a, "names"_a, "record_size"_a, "schema_hash"_a, "lock_timeout"_a, "values"_a,
            "waiter_slots"_a = sharedbox::default_waiter_slots, "publish"_a = true)
        .def_static("attach", &Segment::attach, "name"_a, "names"_a, "schema_hash"_a, "lock_timeout"_a)
        .def("get", &get, "field"_a)
        .def(
            "get_versioned",
            [](const Segment &s, std::uint32_t index) {
                sharedbox::FieldRead read;
                s.read(index, read);
                return nb::make_tuple(read.version, decode_value(*read.field, read.bytes));
            },
            "field"_a)
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
            [](const Segment &s, nb::tuple names) -> nb::dict {
                if (names.size() != s.field_count())
                    throw std::invalid_argument("got " + std::to_string(names.size()) + " field names for " +
                                                std::to_string(s.field_count()) + " fields");
                return sharedbox::with_record(s, [&](const std::byte *record) {
                    nb::dict out;
                    for (std::uint32_t i = 0; i < s.field_count(); ++i)
                        out[names[i]] = decode_value(s.field(i), s.payload(i, record));
                    return out;
                });
            },
            "names"_a)
        .def(
            "update",
            [](Segment &s, nb::dict values) {
                // An update of a few fields, the usual case, needs no allocation for the list.
                sharedbox::Pending few_pending[8];
                std::pair<std::uint32_t, std::string> few[8];
                std::vector<sharedbox::Pending> many_pending;
                Encoded many;
                std::span<sharedbox::Pending> pending(few_pending, values.size());
                std::span<std::pair<std::uint32_t, std::string>> encoded(few, values.size());
                if (values.size() > std::size(few)) {
                    many_pending.resize(values.size());
                    many.resize(values.size());
                    pending = many_pending;
                    encoded = many;
                }
                std::size_t next = 0;
                // Every value is converted before the write, so a bad one writes nothing.
                for (const auto &[key, value] : values) {
                    Py_ssize_t size = 0;
                    const char *text = PyUnicode_AsUTF8AndSize(key.ptr(), &size);
                    if (text == nullptr)
                        throw nb::python_error();
                    const std::string_view name(text, static_cast<std::size_t>(size));
                    const std::optional<std::uint32_t> index = s.index_of(name);
                    if (!index)
                        throw nb::key_error(std::string(name).c_str());
                    pending[next++] = {*index, value.ptr()};
                }
                sharedbox::encode_all(s, pending, encoded);
                s.write(encoded);
            },
            "values"_a)
        .def(
            "set",
            [](Segment &s, const Values &values) {
                Encoded encoded;
                encoded.reserve(values.size());
                for (const auto &[index, value] : values)
                    encoded.emplace_back(index,
                                         sharedbox::encode(s.field(index), s.field_name(index), value.ptr()));
                s.write(encoded);
            },
            "values"_a)
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
                         const std::string_view value = s.payload(i, record);
                         out.put(nb::bytes(value.data(), value.size()));
                     }
                     return out.commit();
                 });
             })
        .def(
            "_write",
            [](Segment &s, const std::vector<std::pair<std::uint32_t, nb::bytes>> &values) {
                Encoded copied;
                copied.reserve(values.size());
                for (const auto &[index, data] : values)
                    copied.emplace_back(index, std::string(data.c_str(), data.size()));
                s.write(copied);
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
            [](Field *self, nb::object spec, nb::object segment_slot) {
                auto read_slot =
                    reinterpret_cast<descrgetfunc>(PyType_GetSlot(Py_TYPE(segment_slot.ptr()), Py_tp_descr_get));
                if (read_slot == nullptr)
                    throw nb::type_error("segment_slot must be a descriptor");
                const auto index = nb::cast<std::uint32_t>(spec.attr("index"));
                new (self) Field{std::move(spec), index, std::move(segment_slot), read_slot};
            },
            "spec"_a, "segment_slot"_a)
        .def_ro("spec", &Field::spec);
}
