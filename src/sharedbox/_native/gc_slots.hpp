#pragma once

#include <nanobind/nanobind.h>

namespace sharedbox {

/// tp_traverse of a bound class T that has `int traverse(visitproc, void *) const`: visits the type, as a heap
/// type must, and T's references once the instance is built.
template <class T> int traverse_slot(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self));
    if (!nanobind::inst_ready(self))
        return 0;
    return nanobind::inst_ptr<T>(self)->traverse(visit, arg);
}

/// tp_clear of a bound class T that has `void clear()`.
template <class T> int clear_slot(PyObject *self) {
    if (nanobind::inst_ready(self))
        nanobind::inst_ptr<T>(self)->clear();
    return 0;
}

/// The two slots above, for a class that needs no other slot.
template <class T>
inline const PyType_Slot gc_slots[] = {{Py_tp_traverse, reinterpret_cast<void *>(traverse_slot<T>)},
                                       {Py_tp_clear, reinterpret_cast<void *>(clear_slot<T>)},
                                       {0, nullptr}};

} // namespace sharedbox
