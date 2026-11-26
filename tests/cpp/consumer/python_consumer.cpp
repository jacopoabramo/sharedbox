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
