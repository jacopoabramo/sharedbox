#include "set.hpp"

#include <cstring>
#include <vector>

#include "scalars.hpp"
#include "types.hpp"

namespace sharedbox {

nb::object NativeSet::copy(nb::pointer_and_handle<NativeSet> self) {
    const NativeSet &s = *self.p;
    std::vector<std::byte> stored;
    {
        nb::ft_lock_guard guard(s.mutex_);
        const std::span<const std::byte> used = s.used();
        stored.assign(used.begin(), used.end());
    }
    return like(self, stored);
}

bool NativeSet::contains(nb::handle value) {
    SlotBuffer key(stride_);
    if (!encode_key(value, key.data(), true))
        return false;
    nb::ft_lock_guard guard(mutex_);
    ensure_checked(0);
    return find(key.data()) != no_slot;
}

void NativeSet::add(nb::handle value) {
    SlotBuffer key(stride_);
    encode_key(value, key.data(), false);
    {
        nb::ft_lock_guard guard(mutex_);
        ensure_checked(0);
        if (find(key.data()) != no_slot)
            return;
        if (count_ < capacity_)
            return append_slot(key.data());
    }
    too_many(std::size_t{capacity_} + 1);
}

void NativeSet::discard(nb::handle value) {
    SlotBuffer key(stride_);
    if (!encode_key(value, key.data(), true))
        return;
    nb::ft_lock_guard guard(mutex_);
    ensure_checked(0);
    if (const std::uint32_t at = find(key.data()); at != no_slot)
        remove_moving_last(at);
}

void NativeSet::remove(nb::handle value) {
    SlotBuffer key(stride_);
    if (encode_key(value, key.data(), true)) {
        nb::ft_lock_guard guard(mutex_);
        ensure_checked(0);
        if (const std::uint32_t at = find(key.data()); at != no_slot)
            return remove_moving_last(at);
    }
    // In a tuple, so a tuple value is the exception's one argument, as for set.remove.
    PyErr_SetObject(PyExc_KeyError, nb::make_tuple(value).ptr());
    throw nb::python_error();
}

nb::object NativeSet::pop() {
    const Types &types = codec();
    SlotBuffer copy(stride_);
    for (;;) {
        std::uint32_t at = no_slot;
        {
            nb::ft_lock_guard guard(mutex_);
            if (count_ != 0) {
                at = count_ - 1;
                std::memcpy(copy.data(), slot(at), stride_);
            }
        }
        if (at == no_slot)
            raise(PyExc_KeyError, "pop from an empty set");
        // Decoded before the slot is removed, so a decode that raises leaves the value as it was.
        nb::object value = types.decode_element(node_, copy.data(), label_);
        // Removed only while the last slot still holds the bytes decoded; when another thread changed it
        // meanwhile, the pop starts again, so two threads never pop the same element.
        nb::ft_lock_guard guard(mutex_);
        if (count_ == at + 1 && std::memcmp(slot(at), copy.data(), stride_) == 0) {
            remove_moving_last(at);
            return value;
        }
    }
}

void NativeSet::remove_all() {
    nb::ft_lock_guard guard(mutex_);
    forget();
    splice(0, count_, nullptr, 0);
    checked_ = true;
}

void NativeSet::refuse_unbuilt(nb::handle value) const {
    if (nb::isinstance<NativeSet>(value) && !nb::inst_ready(value))
        raise(PyExc_TypeError,
              label_ + " got a " +
                  nb::borrow<nb::str>(nb::handle(Py_TYPE(value.ptr())).attr("__qualname__")).c_str() +
                  " that was never built");
}

bool NativeSet::encode_keys(nb::handle iterable, std::vector<std::byte> &out, bool quiet) const {
    const nb::object items = nb::steal(PyObject_GetIter(iterable.ptr()));
    if (!items.is_valid())
        throw nb::python_error();
    for (;;) {
        const nb::object item = nb::steal(PyIter_Next(items.ptr()));
        if (!item.is_valid())
            break;
        const std::size_t at = out.size();
        out.resize(at + stride_);
        if (!encode_key(item, out.data() + at, quiet))
            return false;
    }
    if (PyErr_Occurred())
        throw nb::python_error();
    return true;
}

void NativeSet::update(nb::args iterables) {
    // ponytail: every element is encoded before duplicates are dropped, so a long iterable of few distinct
    // keys costs its length in memory; deduplicate while encoding if that shows up.
    std::vector<std::byte> encoded;
    for (nb::handle iterable : iterables) {
        refuse_unbuilt(iterable);
        encode_keys(iterable, encoded, false);
    }
    std::size_t refused = 0;
    {
        nb::ft_lock_guard guard(mutex_);
        ensure_checked(0);
        const std::uint32_t before = count_;
        for (std::size_t at = 0; at < encoded.size(); at += stride_) {
            const std::byte *key = encoded.data() + at;
            if (find(key) != no_slot)
                continue;
            if (count_ == capacity_) {
                refused = std::size_t{count_} + 1;
                break;
            }
            append_slot(key);
        }
        if (refused != 0) {
            // The keys added are the last slots, so restoring the count removes them.
            forget();
            set_count(before);
        }
    }
    if (refused != 0)
        too_many(refused);
}

nb::object NativeSet::equals(nb::handle other) {
    const bool native = nb::isinstance<NativeSet>(other);
    refuse_unbuilt(other);
    if (!native && !instance(other.ptr(), scalars::set()))
        return nb::borrow(Py_NotImplemented);
    std::vector<std::byte> keys;
    NativeSet *theirs = native ? nb::inst_ptr<NativeSet>(other) : nullptr;
    if (theirs != nullptr &&
        theirs->has_elements(codec().element_text(node_), codec().element_annotation(node_))) {
        nb::ft_lock_guard guard(theirs->mutex_);
        theirs->ensure_checked(0);
        keys.assign(theirs->slot(0), theirs->slot(theirs->count_));
    } else {
        {
            nb::ft_lock_guard guard(mutex_);
            ensure_checked(0);
        }
        const Py_ssize_t length = PyObject_Size(other.ptr());
        if (length < 0)
            throw nb::python_error();
        if (static_cast<std::size_t>(length) != size())
            return nb::bool_(false);
        if (!encode_keys(other, keys, true))
            return nb::bool_(false);
    }
    nb::ft_lock_guard guard(mutex_);
    ensure_checked(0);
    if (keys.size() != std::size_t{count_} * stride_)
        return nb::bool_(false);
    // Two keys of other may encode to the same bytes, so equality also needs every slot found once.
    std::vector<bool> found(count_);
    for (std::size_t at = 0; at < keys.size(); at += stride_) {
        const std::uint32_t mine = find(keys.data() + at);
        if (mine == no_slot || found[mine])
            return nb::bool_(false);
        found[mine] = true;
    }
    return nb::bool_(true);
}

} // namespace sharedbox
