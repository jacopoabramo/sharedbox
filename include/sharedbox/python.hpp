// Takes a box or a stream from a Python object that offers one through __sharedbox_box__ or
// __sharedbox_stream__, checks its schema hash and sets a Python exception when either step fails.
// Every function here calls into Python, so the calling thread holds the GIL, or on a free-threaded
// build is attached to the interpreter. Only functions of the Limited API are called.
#pragma once

#include <Python.h>

#include <sharedbox/sharedbox.hpp>

#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <optional>
#include <utility>

namespace sharedbox {
inline namespace v3 {
namespace python {

namespace detail {

struct kind_names {
    const char *dunder;
    const char *capsule;
    const char *used;
    const char *noun;
    std::uint16_t major;
    std::uint16_t minor;
};

inline constexpr kind_names box_names{"__sharedbox_box__", "sharedbox_box", "used_sharedbox_box", "box",
                                      layout_major,        layout_minor};
inline constexpr kind_names stream_names{"__sharedbox_stream__",  "sharedbox_stream",
                                         "used_sharedbox_stream", "stream",
                                         stream_layout_major,     stream_layout_minor};

// Calls the dunder with max_version and returns the capsule, or nullptr with an exception set.
inline PyObject *call_dunder(PyObject *obj, const kind_names &names) {
    PyObject *method = PyObject_GetAttrString(obj, names.dunder);
    if (method == nullptr) {
        if (PyErr_ExceptionMatches(PyExc_AttributeError)) {
            PyErr_Clear();
            PyObject *type = PyObject_Type(obj);
            PyObject *type_name =
                type == nullptr ? nullptr : PyType_GetName(reinterpret_cast<PyTypeObject *>(type));
            Py_XDECREF(type);
            if (type_name != nullptr) {
                PyErr_Format(PyExc_TypeError, "expected a %s, got %U", names.noun, type_name);
                Py_DECREF(type_name);
            }
        }
        return nullptr;
    }
    PyObject *capsule = PyObject_CallFunction(method, "((HH))", names.major, names.minor);
    Py_DECREF(method);
    return capsule;
}

template <class T> std::optional<T> take(PyObject *obj, std::uint64_t schema, const kind_names &names) {
    PyObject *capsule = call_dunder(obj, names);
    if (capsule == nullptr)
        return std::nullopt;
    auto *capsule_handle = static_cast<sbx_handle *>(PyCapsule_GetPointer(capsule, names.capsule));
    if (capsule_handle == nullptr) {
        Py_DECREF(capsule);
        return std::nullopt;
    }
    result<T> taken = T::from_capsule(capsule_handle);
    if (!taken) {
        Py_DECREF(capsule);
        PyErr_Format(PyExc_ValueError, "cannot open the %s: status %d", names.noun,
                     static_cast<int>(taken.error().code));
        return std::nullopt;
    }
    // The handle is ours now; the new name keeps the capsule's destructor from releasing it.
    int renamed = PyCapsule_SetName(capsule, names.used);
    Py_DECREF(capsule);
    if (renamed != 0)
        return std::nullopt;
    if (taken->schema_hash() != schema) {
        char message[64];
        std::snprintf(message, sizeof message, "expected schema 0x%" PRIx64 ", got 0x%" PRIx64, schema,
                      taken->schema_hash());
        PyErr_SetString(PyExc_TypeError, message);
        return std::nullopt;
    }
    return std::optional<T>(std::move(*taken));
}

} // namespace detail

// The box obj offers, or std::nullopt with a Python exception set: TypeError when obj offers no box or
// the box's schema hash is not schema, ValueError when the capsule cannot be opened, or what the
// dunder raised.
inline std::optional<handle> take_box(PyObject *obj, std::uint64_t schema) {
    return detail::take<handle>(obj, schema, detail::box_names);
}

// The stream obj offers, or std::nullopt with a Python exception set, as take_box does.
inline std::optional<stream> take_stream(PyObject *obj, std::uint64_t schema) {
    return detail::take<stream>(obj, schema, detail::stream_names);
}

} // namespace python
} // namespace v3
} // namespace sharedbox
