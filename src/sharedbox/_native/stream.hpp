#pragma once

#include <nanobind/nanobind.h>

namespace sharedbox {

/// Adds Stream, Sender, Reader and the stream errors to the module.
void bind_stream(nanobind::module_ &m);

/// The destructor of a capsule named name that holds a heap sbx_handle. A consumer that takes the handle
/// renames the capsule used_name and releases the handle itself; the struct's memory is freed here either
/// way. PyCapsule_IsValid sets no error, so a destructor running while an exception is pending leaves it
/// alone.
void release_handle_capsule(PyObject *capsule, const char *name, const char *used_name);

} // namespace sharedbox
