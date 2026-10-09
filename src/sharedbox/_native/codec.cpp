#include "codec.hpp"
#include "types.hpp"

#include <nanobind/nanobind.h>
#include <sharedbox/sharedbox.hpp>

#include <algorithm>
#include <cstring>
#include <string_view>

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
    case FieldKind::Ref:
        return "a reference";
    }
    return "?";
}

[[noreturn]] void raise(PyObject *type, const std::string &message) {
    PyErr_SetString(type, message.c_str());
    throw nb::python_error();
}

std::uint64_t to_u64(PyObject *number, const std::string &name, const char *what) {
    const unsigned long long value = PyLong_AsUnsignedLongLong(number);
    if (value == static_cast<unsigned long long>(-1) && PyErr_Occurred()) {
        if (!PyErr_ExceptionMatches(PyExc_OverflowError))
            throw nb::python_error();
        PyErr_Clear();
        raise(PyExc_ValueError, name + " needs a " + what + " of 0 to 2**64 - 1");
    }
    return value;
}

// Releases the buffer on every way out of encode(), exceptions included.
class Buffer {
public:
    explicit Buffer(PyObject *value) {
        if (PyObject_GetBuffer(value, &view, PyBUF_FULL_RO) != 0)
            throw nb::python_error();
    }
    ~Buffer() { PyBuffer_Release(&view); }
    Buffer(const Buffer &) = delete;
    Buffer &operator=(const Buffer &) = delete;

    Py_buffer view;
};

} // namespace

std::span<const std::byte> encode(const FieldDesc &field, const std::string &name, PyObject *value,
                                  EncodeBuffer &buffer) {
    static constexpr std::byte bool_bytes[2] = {std::byte{0}, std::byte{1}};
    std::size_t size = 0;
    switch (field.kind) {
    case FieldKind::Bool: {
        if (!PyBool_Check(value))
            goto wrong_type;
        return {&bool_bytes[value == Py_True ? 1 : 0], 1};
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
        std::memcpy(buffer.small, &number, 8);
        return {buffer.small, 8};
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
        std::memcpy(buffer.small, &number, 8);
        return {buffer.small, 8};
    }
    case FieldKind::Str: {
        if (!PyUnicode_Check(value))
            goto wrong_type;
        Py_ssize_t length = 0;
        // The UTF-8 text belongs to value and lasts as long as it does.
        const char *data = PyUnicode_AsUTF8AndSize(value, &length);
        if (data == nullptr)
            throw nb::python_error();
        size = static_cast<std::size_t>(length);
        if (size > field.capacity)
            goto too_long;
        return std::as_bytes(std::span(data, size));
    }
    case FieldKind::Bytes: {
        if (!PyBytes_Check(value) && !PyByteArray_Check(value) && !PyMemoryView_Check(value))
            goto wrong_type;
        Buffer view(value);
        size = static_cast<std::size_t>(view.view.len);
        if (size > field.capacity)
            goto too_long;
        // Copied, since a bytearray can change while a write waits for the lock without the GIL.
        std::byte *out = buffer.small;
        if (size > sizeof buffer.small) {
            buffer.large.reset(new std::byte[size]);
            out = buffer.large.get();
        }
        // Copies a strided memoryview in logical order, as bytes() would.
        if (PyBuffer_ToContiguous(out, &view.view, view.view.len, 'C') != 0)
            throw nb::python_error();
        return {out, size};
    }
    case FieldKind::Ref: {
        box_ref ref{};
        if (value != Py_None) {
            // The Python layer passes (create_id, schema_hash, name) of the box assigned.
            if (!PyTuple_Check(value) || PyTuple_Size(value) != 3 || !PyLong_Check(PyTuple_GetItem(value, 0)) ||
                !PyLong_Check(PyTuple_GetItem(value, 1)) || !PyUnicode_Check(PyTuple_GetItem(value, 2)))
                goto wrong_type;
            ref.create_id = to_u64(PyTuple_GetItem(value, 0), name, "create id");
            ref.schema_hash = to_u64(PyTuple_GetItem(value, 1), name, "schema hash");
            if (ref.create_id == 0)
                raise(PyExc_ValueError, name + " needs a nonzero create id");
            Py_ssize_t length = 0;
            const char *text = PyUnicode_AsUTF8AndSize(PyTuple_GetItem(value, 2), &length);
            if (text == nullptr)
                throw nb::python_error();
            const std::string_view box_name(text, static_cast<std::size_t>(length));
            if (!detail::name_ok(box_name))
                raise(PyExc_ValueError,
                      name +
                          " needs a box name of segments of [A-Za-z0-9_-] joined by ':', at most 240 characters");
            std::memcpy(ref.name, box_name.data(), box_name.size());
        }
        std::memcpy(buffer.small, &ref, sizeof ref);
        return {buffer.small, sizeof ref};
    }
    }
    raise(PyExc_SystemError, "unknown field kind");
too_long:
    raise(PyExc_ValueError, name + " holds at most " + std::to_string(field.capacity) +
                                " bytes; the value encodes to " + std::to_string(size));
wrong_type:
    nb::object type_name = nb::handle(reinterpret_cast<PyObject *>(Py_TYPE(value))).attr("__name__");
    raise(PyExc_TypeError,
          name + " expects " + kind_name(field.kind) + ", got " + nb::borrow<nb::str>(type_name).c_str());
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
    case FieldKind::Ref: {
        box_ref ref;
        std::memcpy(&ref, data, sizeof ref);
        if (ref.create_id == 0)
            return Py_NewRef(Py_None);
        const char *end = std::find(ref.name, ref.name + sizeof ref.name, '\0');
        PyObject *text = PyUnicode_DecodeUTF8(ref.name, end - ref.name, "replace");
        if (text == nullptr)
            return nullptr;
        return Py_BuildValue("(KKN)", static_cast<unsigned long long>(ref.create_id),
                             static_cast<unsigned long long>(ref.schema_hash), text);
    }
    }
    return nullptr;
}

void encode_all(const Segment &s, const Types *types, std::span<const Pending> values,
                std::span<EncodeBuffer> buffers, std::span<value> out) {
    const std::span<const FieldDesc> fields = s.fields();
    const std::span<const std::string> names = s.field_names();
    for (std::size_t i = 0; i < values.size(); ++i) {
        const std::uint32_t index = values[i].field;
        // Checked before the narrowing cast, which would otherwise turn index 65536 into field 0.
        check_index(index, fields.size());
        const FieldDesc &field = fields[index];
        // The members are written separately because MSVC copies a value built in one step through
        // a stack temporary and reloads it, which stalls for about 10 ns per update.
        const std::span<const std::byte> bytes =
            static_cast<std::uint8_t>(field.kind) <= kind_ref
                ? encode(field, names[index], values[i].value, buffers[i])
                : need(types, index).encode(index, values[i].value, buffers[i]);
        out[i].field = static_cast<std::uint16_t>(index);
        out[i].bytes = bytes;
    }
}

} // namespace sharedbox
