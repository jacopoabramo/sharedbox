#include "codec.hpp"

#include <nanobind/nanobind.h>

#include <cstring>

namespace nb = nanobind;

namespace sharedbox {
namespace {

const char *kind_name(FieldKind kind) {
    switch (kind) {
    case FieldKind::Bool:
        return "bool";
    case FieldKind::Int:
        return "int";
    case FieldKind::Float:
        return "float";
    case FieldKind::Str:
        return "str";
    case FieldKind::Bytes:
        return "bytes";
    }
    return "?";
}

[[noreturn]] void raise(PyObject *type, const std::string &message) {
    PyErr_SetString(type, message.c_str());
    throw nb::python_error();
}

[[noreturn]] void wrong_type(const FieldDesc &field, const std::string &name, PyObject *value) {
    nb::object type_name = nb::handle(reinterpret_cast<PyObject *>(Py_TYPE(value))).attr("__name__");
    raise(PyExc_TypeError,
          name + " expects " + kind_name(field.kind) + ", got " + nb::borrow<nb::str>(type_name).c_str());
}

[[noreturn]] void too_long(const FieldDesc &field, const std::string &name, std::size_t size) {
    raise(PyExc_ValueError, name + " holds at most " + std::to_string(field.capacity) +
                                " bytes; the value encodes to " + std::to_string(size));
}

} // namespace

std::string encode(const FieldDesc &field, const std::string &name, PyObject *value) {
    std::size_t size = 0;
    switch (field.kind) {
    case FieldKind::Bool: {
        if (!PyBool_Check(value))
            goto wrong_type;
        return std::string(1, value == Py_True ? '\x01' : '\x00');
    }
    case FieldKind::Int: {
        if (PyBool_Check(value) || !PyLong_Check(value))
            goto wrong_type;
        int overflow = 0;
        long long number = PyLong_AsLongLongAndOverflow(value, &overflow);
        if (overflow != 0)
            raise(PyExc_OverflowError, name + " holds a signed 64-bit integer; the value does not fit");
        if (number == -1 && PyErr_Occurred())
            throw nb::python_error();
        std::string out(8, '\0');
        std::memcpy(out.data(), &number, 8);
        return out;
    }
    case FieldKind::Float: {
        if (PyBool_Check(value) || !(PyFloat_Check(value) || PyLong_Check(value)))
            goto wrong_type;
        double number = PyFloat_AsDouble(value);
        if (number == -1.0 && PyErr_Occurred()) {
            if (!PyErr_ExceptionMatches(PyExc_OverflowError))
                throw nb::python_error();
            PyErr_Clear();
            raise(PyExc_OverflowError, name + " holds a 64-bit float; the value does not fit");
        }
        std::string out(8, '\0');
        std::memcpy(out.data(), &number, 8);
        return out;
    }
    case FieldKind::Str: {
        if (!PyUnicode_Check(value))
            goto wrong_type;
        Py_ssize_t length = 0;
        const char *data = PyUnicode_AsUTF8AndSize(value, &length);
        if (data == nullptr)
            throw nb::python_error();
        size = static_cast<std::size_t>(length);
        if (size > field.capacity)
            goto too_long;
        return std::string(data, size);
    }
    case FieldKind::Bytes: {
        if (PyUnicode_Check(value) || !PyObject_CheckBuffer(value))
            goto wrong_type;
        Py_buffer view;
        if (PyObject_GetBuffer(value, &view, PyBUF_SIMPLE) != 0)
            throw nb::python_error();
        size = static_cast<std::size_t>(view.len);
        if (size > field.capacity) {
            PyBuffer_Release(&view);
            goto too_long;
        }
        std::string out(static_cast<const char *>(view.buf), size);
        PyBuffer_Release(&view);
        return out;
    }
    }
    raise(PyExc_SystemError, "unknown field kind");
wrong_type:
    wrong_type(field, name, value);
too_long:
    too_long(field, name, size);
}

PyObject *decode(const FieldDesc &field, const char *data, std::size_t size) {
    switch (field.kind) {
    case FieldKind::Bool:
        return PyBool_FromLong(data[0] != 0);
    case FieldKind::Int: {
        long long number;
        std::memcpy(&number, data, 8);
        return PyLong_FromLongLong(number);
    }
    case FieldKind::Float: {
        double number;
        std::memcpy(&number, data, 8);
        return PyFloat_FromDouble(number);
    }
    case FieldKind::Str:
        return PyUnicode_DecodeUTF8(data, static_cast<Py_ssize_t>(size), "replace");
    case FieldKind::Bytes:
        return PyBytes_FromStringAndSize(data, static_cast<Py_ssize_t>(size));
    }
    return nullptr;
}

} // namespace sharedbox
