#pragma once

#include <Python.h>

#include <cstddef>
#include <cstdint>
#include <span>
#include <string>

namespace sharedbox::scalars {

/// Imports the classes conversions need; call once, from the module's init.
void init();
/// Whether value is a Python value of kind 6 to 12: complex also takes float and int, not bool; date
/// refuses datetime.
bool accepts(std::uint8_t kind, PyObject *value);
/// Writes value, of kind 6 to 11, into out, which holds the kind's size; raises a Python exception
/// naming name.
void encode(std::uint8_t kind, PyObject *value, std::span<std::byte> out, const std::string &name);
/// The text stored for a Decimal; raises if it takes more than capacity bytes.
std::string decimal_text(PyObject *value, std::uint32_t capacity, const std::string &name);
/// A new reference to the value of kind 6 to 12 in bytes (for a decimal, its text without the length),
/// or nullptr with a Python exception set. A stored value that fails the decoding checks raises
/// ValueError naming name.
PyObject *decode(std::uint8_t kind, std::span<const std::byte> bytes, const std::string &name);
/// collections.abc.Mapping, Sequence and Set, borrowed.
PyObject *mapping();
PyObject *sequence();
PyObject *set();

} // namespace sharedbox::scalars
