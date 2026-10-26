#include "set.hpp"

#include <algorithm>
#include <cstring>
#include <vector>

#include "scalars.hpp"
#include "types.hpp"

namespace sharedbox {

nb::object NativeSet::copy(nb::pointer_and_handle<NativeSet> self) {
    const NativeSet &s = *self.p;
    std::vector<std::byte> stored;
    bool checked = false;
    {
        nb::ft_lock_guard guard(s.mutex_);
        const std::span<const std::byte> used = s.used();
        stored.assign(used.begin(), used.end());
        checked = s.checked_;
    }
    nb::object out = like(self, stored);
    // Copied from checked keys, so the copy's keys need no check either; no other thread has it yet.
    nb::inst_ptr<NativeSet>(out)->checked_ = checked;
    return out;
}

bool NativeSet::contains(nb::handle value) {
    SlotBuffer key(stride_);
    if (!encode_key(value, key.data(), true))
        return false;
    nb::ft_lock_guard guard(mutex_);
    return lookup(key.data()) != no_slot;
}

void NativeSet::add(nb::handle value) {
    SlotBuffer key(stride_);
    encode_key(value, key.data(), false);
    {
        nb::ft_lock_guard guard(mutex_);
        if (lookup(key.data()) != no_slot)
            return;
        // Duplicates another program stored may fill the capacity; dropping them may make room.
        if (count_ == capacity_)
            ensure_checked();
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
    remove_key(key.data());
}

void NativeSet::remove(nb::handle value) {
    SlotBuffer key(stride_);
    if (encode_key(value, key.data(), true)) {
        nb::ft_lock_guard guard(mutex_);
        if (remove_key(key.data()))
            return;
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
            ensure_checked();
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

bool NativeSet::takes(nb::handle other) const {
    refuse_unbuilt(other);
    return nb::isinstance<NativeSet>(other) || instance(other.ptr(), scalars::set());
}

bool NativeSet::encode_keys(nb::handle iterable, std::vector<std::byte> &out, bool quiet) const {
    const nb::object items = nb::steal(PyObject_GetIter(iterable.ptr()));
    if (!items.is_valid())
        throw nb::python_error();
    bool whole = true;
    for (;;) {
        const nb::object item = nb::steal(PyIter_Next(items.ptr()));
        if (!item.is_valid())
            break;
        const std::size_t at = out.size();
        out.resize(at + stride_);
        if (!encode_key(item, out.data() + at, quiet)) {
            out.resize(at);
            whole = false;
        }
    }
    if (PyErr_Occurred())
        throw nb::python_error();
    return whole;
}

bool NativeSet::keys_of(nb::handle other, std::vector<std::byte> &out, bool quiet) const {
    if (nb::isinstance<NativeSet>(other)) {
        NativeSet &theirs = *nb::inst_ptr<NativeSet>(other);
        if (theirs.has_elements(codec().element_text(node_), codec().element_annotation(node_))) {
            nb::ft_lock_guard guard(theirs.mutex_);
            theirs.ensure_checked();
            out.assign(theirs.slot(0), theirs.slot(theirs.count_));
            return true;
        }
    }
    return encode_keys(other, out, quiet);
}

std::size_t NativeSet::match(const std::vector<std::byte> &keys, std::vector<bool> &found,
                             std::vector<std::byte> *extra) {
    ensure_checked();
    found.assign(count_, false);
    std::size_t missing = 0;
    for (std::size_t at = 0; at < keys.size(); at += stride_) {
        const std::byte *key = keys.data() + at;
        if (const std::uint32_t mine = find(key); mine != no_slot) {
            found[mine] = true;
            continue;
        }
        ++missing;
        if (extra != nullptr)
            extra->insert(extra->end(), key, key + stride_);
    }
    return missing;
}

std::size_t NativeSet::append_new(const std::vector<std::byte> &keys) {
    const std::uint32_t before = count_;
    for (std::size_t at = 0; at < keys.size(); at += stride_) {
        const std::byte *key = keys.data() + at;
        if (find(key) != no_slot)
            continue;
        if (count_ == capacity_) {
            // The keys added are the last slots, so restoring the count removes them.
            forget();
            set_count(before);
            return std::size_t{capacity_} + 1;
        }
        append_slot(key);
    }
    return 0;
}

void NativeSet::update(nb::args iterables) {
    // ponytail: every element is encoded before duplicates are dropped, so a long iterable of few distinct
    // keys costs its length in memory; deduplicate while encoding if that shows up.
    std::vector<std::byte> encoded;
    std::vector<std::byte> part;
    for (nb::handle iterable : iterables) {
        refuse_unbuilt(iterable);
        part.clear();
        keys_of(iterable, part, false);
        encoded.insert(encoded.end(), part.begin(), part.end());
    }
    std::size_t refused = 0;
    {
        nb::ft_lock_guard guard(mutex_);
        ensure_checked();
        refused = append_new(encoded);
    }
    if (refused != 0)
        too_many(refused);
}

nb::object NativeSet::equals(nb::handle other) {
    if (!takes(other))
        return nb::borrow(Py_NotImplemented);
    // A NativeSet's length may count stored duplicates, so only another set's is compared first.
    if (!nb::isinstance<NativeSet>(other)) {
        {
            nb::ft_lock_guard guard(mutex_);
            ensure_checked();
        }
        const Py_ssize_t length = PyObject_Size(other.ptr());
        if (length < 0)
            throw nb::python_error();
        if (static_cast<std::size_t>(length) != size())
            return nb::bool_(false);
    }
    std::vector<std::byte> keys;
    if (!keys_of(other, keys, true))
        return nb::bool_(false);
    std::vector<bool> found;
    nb::ft_lock_guard guard(mutex_);
    // Two keys of other may encode to the same bytes, so equality also needs as many keys as slots.
    return nb::bool_(match(keys, found, nullptr) == 0 && keys.size() == std::size_t{count_} * stride_ &&
                     std::find(found.begin(), found.end(), false) == found.end());
}

namespace {

/// Whether the elements of the operand other than the NativeSet can be in the result of op.
bool adds(set_op op, bool reflected) {
    return op == set_op::join || op == set_op::symmetric || (reflected && op == set_op::difference);
}

} // namespace

std::size_t NativeSet::apply(const std::vector<std::byte> &keys, set_op op, bool reflected) {
    const bool add = adds(op, reflected);
    const bool none_kept = reflected && op == set_op::difference;
    const bool keep_found = !none_kept && (op == set_op::join || op == set_op::common);
    const bool keep_rest = !none_kept && op != set_op::common;
    std::vector<bool> found;
    std::vector<std::byte> extra;
    nb::ft_lock_guard guard(mutex_);
    match(keys, found, add ? &extra : nullptr);
    const std::uint32_t count = count_;
    std::vector<std::byte> before;
    std::uint32_t kept = 0;
    for (std::uint32_t at = 0; at < count; ++at) {
        if (found[at] ? keep_found : keep_rest) {
            if (kept != at)
                std::memcpy(slot(kept), slot(at), stride_);
            ++kept;
        } else if (add && before.empty()) {
            // Copied before the first slot moves, so a result past the capacity can be put back.
            const std::span<const std::byte> bytes = used();
            before.assign(bytes.begin(), bytes.end());
        }
    }
    if (kept != count) {
        forget();
        set_count(kept);
    }
    if (!add)
        return 0;
    const std::size_t refused = append_new(extra);
    if (refused != 0 && !before.empty()) {
        std::memcpy(data_.get(), before.data(), before.size());
        set_count(count);
    }
    return refused;
}

nb::object NativeSet::combine(nb::pointer_and_handle<NativeSet> self, nb::handle other, set_op op,
                              bool reflected) {
    const NativeSet &s = *self.p;
    if (!s.takes(other))
        return nb::borrow(Py_NotImplemented);
    std::vector<std::byte> keys;
    s.keys_of(other, keys, !adds(op, reflected));
    nb::object out = copy(self);
    if (const std::size_t refused = nb::inst_ptr<NativeSet>(out)->apply(keys, op, reflected); refused != 0)
        s.too_many(refused);
    return out;
}

nb::object NativeSet::combine_in_place(nb::pointer_and_handle<NativeSet> self, nb::handle other, set_op op) {
    NativeSet &s = *self.p;
    if (!s.takes(other))
        return nb::borrow(Py_NotImplemented);
    std::vector<std::byte> keys;
    s.keys_of(other, keys, !adds(op, false));
    if (const std::size_t refused = s.apply(keys, op, false); refused != 0)
        s.too_many(refused);
    return nb::borrow(self.h);
}

nb::object NativeSet::compare(nb::handle other, int op) {
    if (!takes(other))
        return nb::borrow(Py_NotImplemented);
    std::vector<std::byte> keys;
    // An element of other that is not a key is one this value does not hold.
    const bool whole = keys_of(other, keys, true);
    std::vector<bool> found;
    nb::ft_lock_guard guard(mutex_);
    const bool other_has_more = match(keys, found, nullptr) != 0 || !whole;
    const bool other_has_all = std::find(found.begin(), found.end(), false) == found.end();
    switch (op) {
    case Py_LE:
        return nb::bool_(other_has_all);
    case Py_LT:
        return nb::bool_(other_has_all && other_has_more);
    case Py_GE:
        return nb::bool_(!other_has_more);
    default:
        return nb::bool_(!other_has_more && !other_has_all);
    }
}

bool NativeSet::isdisjoint(nb::handle iterable) {
    refuse_unbuilt(iterable);
    std::vector<std::byte> keys;
    keys_of(iterable, keys, true);
    std::vector<bool> found;
    nb::ft_lock_guard guard(mutex_);
    return match(keys, found, nullptr) == keys.size() / stride_;
}

} // namespace sharedbox
