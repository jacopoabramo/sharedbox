#pragma once

#include <Python.h>

#include <cstddef>
#include <string>

#include "segment.hpp"

namespace sharedbox {

/// The bytes stored for value; raises a Python exception naming the field.
std::string encode(const FieldDesc &field, const std::string &name, PyObject *value);

/// A new reference to the value stored in data.
PyObject *decode(const FieldDesc &field, const char *data, std::size_t size);

} // namespace sharedbox
