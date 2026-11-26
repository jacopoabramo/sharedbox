// A C++ library that takes boxes and streams from Python objects with sharedbox/python.hpp;
// tests/test_capsule.py loads it with ctypes.PyDLL, so the exceptions it sets reach the tests.
#include <sharedbox/python.hpp>

#include <cstdint>
#include <span>

#ifdef _WIN32
#define PYCONSUMER_API extern "C" __declspec(dllexport)
#else
#define PYCONSUMER_API extern "C" __attribute__((visibility("default")))
#endif

// Returns the int field of the box obj offers, or nullptr with an exception set.
PYCONSUMER_API PyObject *py_read_int(PyObject *obj, std::uint64_t schema, std::uint16_t field) {
    std::optional<sharedbox::handle> box = sharedbox::python::take_box(obj, schema);
    if (!box)
        return nullptr;
    std::int64_t value = 0;
    auto read = box->read(field, std::as_writable_bytes(std::span(&value, 1)));
    if (!read) {
        PyErr_Format(PyExc_RuntimeError, "read failed: %d", static_cast<int>(read.error().code));
        return nullptr;
    }
    return PyLong_FromLongLong(value);
}

// Sends count items value, value + 1, ... through the stream obj offers, then closes the sender; None, or
// nullptr with an exception set.
PYCONSUMER_API PyObject *py_send_ints(PyObject *obj, std::uint64_t schema, std::int64_t value, std::uint32_t count,
                                      double timeout) {
    std::optional<sharedbox::stream> stream = sharedbox::python::take_stream(obj, schema);
    if (!stream)
        return nullptr;
    sharedbox::result<sharedbox::stream_sender> sender = stream->sender();
    if (!sender) {
        PyErr_Format(PyExc_RuntimeError, "send failed: %d", static_cast<int>(sender.error().code));
        return nullptr;
    }
    if (sender->item_size() != sizeof(std::int64_t)) {
        PyErr_SetString(PyExc_RuntimeError, "item size");
        return nullptr;
    }
    for (std::uint32_t i = 0; i < count; ++i) {
        const std::int64_t item = value + i;
        const auto sent = sender->send(std::as_bytes(std::span{&item, 1}), sharedbox::seconds(timeout));
        if (!sent) {
            PyErr_Format(PyExc_RuntimeError, "send failed: %d", static_cast<int>(sent.error().code));
            return nullptr;
        }
    }
    Py_RETURN_NONE;
}
