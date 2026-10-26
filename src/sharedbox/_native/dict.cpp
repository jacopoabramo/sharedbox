#include "dict.hpp"

#include <cstring>
#include <vector>

#include "scalars.hpp"
#include "types.hpp"

namespace sharedbox {

nb::object NativeDict::copy(nb::pointer_and_handle<NativeDict> self) {
    const NativeDict &d = *self.p;
    std::vector<std::byte> stored;
    bool checked = false;
    {
        nb::ft_lock_guard guard(d.mutex_);
        const std::span<const std::byte> used = d.used();
        stored.assign(used.begin(), used.end());
        checked = d.checked_;
    }
    nb::object out = like(self, stored);
    // Copied from checked keys, so the copy's keys need no check either; no other thread has it yet.
    nb::inst_ptr<NativeDict>(out)->checked_ = checked;
    return out;
}

nb::object NativeDict::find_value(nb::handle key) {
    const Types &types = codec();
    SlotBuffer found(stride_);
    if (!encode_key(key, found.data(), true))
        return {};
    {
        nb::ft_lock_guard guard(mutex_);
        const std::uint32_t at = lookup(found.data());
        if (at == no_slot)
            return {};
        // Decoded from a copy, because decoding may run Python code, which could change this value.
        std::memcpy(found.data(), slot(at), stride_);
    }
    return types.decode_member(node_, 1, found.data(), label_);
}

nb::object NativeDict::getitem(nb::handle key) {
    nb::object value = find_value(key);
    if (value.is_valid())
        return value;
    // In a tuple, so a tuple key is the exception's one argument, as for dict.
    PyErr_SetObject(PyExc_KeyError, nb::make_tuple(key).ptr());
    throw nb::python_error();
}

nb::object NativeDict::get(nb::handle key, nb::handle fallback) {
    nb::object value = find_value(key);
    return value.is_valid() ? value : nb::borrow(fallback);
}

bool NativeDict::contains(nb::handle key) {
    SlotBuffer encoded(stride_);
    if (!encode_key(key, encoded.data(), true))
        return false;
    nb::ft_lock_guard guard(mutex_);
    return lookup(encoded.data()) != no_slot;
}

void NativeDict::refuse_unbuilt(nb::handle value) const {
    if (nb::isinstance<NativeDict>(value) && !nb::inst_ready(value))
        raise(PyExc_TypeError,
              label_ + " got a " +
                  nb::borrow<nb::str>(nb::handle(Py_TYPE(value.ptr())).attr("__qualname__")).c_str() +
                  " that was never built");
}

nb::object NativeDict::equals(nb::handle other) {
    refuse_unbuilt(other);
    if (!instance(other.ptr(), scalars::mapping()))
        return nb::borrow(Py_NotImplemented);
    const Types &types = codec();
    std::uint32_t count = 0;
    {
        nb::ft_lock_guard guard(mutex_);
        // Stored duplicates would count twice in the length.
        ensure_checked();
        count = count_;
    }
    const Py_ssize_t length = PyObject_Size(other.ptr());
    if (length < 0)
        throw nb::python_error();
    if (static_cast<std::size_t>(length) != count)
        return nb::bool_(false);
    const nb::object items = nb::steal(PyMapping_Items(other.ptr()));
    if (!items.is_valid())
        throw nb::python_error();
    // Two keys of other may encode to the same bytes, so each must find a slot no other key found.
    std::vector<bool> found(count, false);
    SlotBuffer mine(stride_);
    for (nb::handle pair : items) {
        if (!PyTuple_Check(pair.ptr()) || PyTuple_Size(pair.ptr()) != 2)
            return nb::bool_(false);
        if (!encode_key(nb::handle(PyTuple_GetItem(pair.ptr(), 0)), mine.data(), true))
            return nb::bool_(false);
        {
            nb::ft_lock_guard guard(mutex_);
            const std::uint32_t at = lookup(mine.data());
            if (at == no_slot || at >= found.size() || found[at])
                return nb::bool_(false);
            found[at] = true;
            std::memcpy(mine.data(), slot(at), stride_);
        }
        const nb::object value = types.decode_member(node_, 1, mine.data(), label_);
        const int equal = PyObject_RichCompareBool(value.ptr(), PyTuple_GetItem(pair.ptr(), 1), Py_EQ);
        if (equal < 0)
            throw nb::python_error();
        if (equal == 0)
            return nb::bool_(false);
    }
    return nb::bool_(true);
}

} // namespace sharedbox
