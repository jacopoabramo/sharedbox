#include "dict.hpp"

#include <cstring>
#include <string>
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

void NativeDict::encode_item(nb::handle key, nb::handle value, std::vector<std::byte> &out) const {
    const std::size_t at = out.size();
    out.resize(at + stride_);
    encode_key(key, out.data() + at, false);
    codec().encode_member(node_, 1, value.ptr(), out.data() + at, label_);
}

void NativeDict::setitem(nb::handle key, nb::handle value) {
    std::vector<std::byte> item;
    encode_item(key, value, item);
    {
        nb::ft_lock_guard guard(mutex_);
        // On a scan, lookup returns the last slot holding key, whose value ensure_checked later keeps.
        if (const std::uint32_t at = lookup(item.data()); at != no_slot) {
            std::memcpy(slot(at) + value_offset_, item.data() + value_offset_, stride_ - value_offset_);
            return;
        }
        // Duplicates another program stored may fill the capacity; dropping them may make room.
        if (count_ == capacity_)
            ensure_checked();
        if (count_ < capacity_)
            return append_slot(item.data());
    }
    too_many(std::size_t{capacity_} + 1);
}

std::uint32_t NativeDict::locate(const std::byte *key, bool &scanning) {
    scanning = !checked_ && scans_ < scans_before_index;
    return lookup(key);
}

void NativeDict::remove_at(std::uint32_t at, const std::byte *key, bool scanning) {
    // ponytail: O(n) per removal; mark removed slots and compact them in copy_to and iteration when a benchmark
    // shows the cost
    splice(at, at + 1, nullptr, 0);
    // A scan found the last slot holding key, so only earlier slots may hold another.
    if (scanning)
        while ((at = scan(key, at)) != no_slot)
            splice(at, at + 1, nullptr, 0);
    forget();
}

namespace {

[[noreturn]] void missing_key(nb::handle key) {
    // In a tuple, so a tuple key is the exception's one argument, as for dict.
    PyErr_SetObject(PyExc_KeyError, nb::make_tuple(key).ptr());
    throw nb::python_error();
}

} // namespace

void NativeDict::delitem(nb::handle key) {
    SlotBuffer encoded(stride_);
    if (encode_key(key, encoded.data(), true)) {
        nb::ft_lock_guard guard(mutex_);
        bool scanning = false;
        if (const std::uint32_t at = locate(encoded.data(), scanning); at != no_slot)
            return remove_at(at, encoded.data(), scanning);
    }
    missing_key(key);
}

nb::object NativeDict::pop(nb::handle key, nb::handle fallback) {
    const Types &types = codec();
    SlotBuffer encoded(stride_);
    if (encode_key(key, encoded.data(), true)) {
        SlotBuffer copy(stride_);
        for (;;) {
            bool scanning = false;
            std::uint32_t at = no_slot;
            {
                nb::ft_lock_guard guard(mutex_);
                at = locate(encoded.data(), scanning);
                if (at != no_slot)
                    std::memcpy(copy.data(), slot(at), stride_);
            }
            if (at == no_slot)
                break;
            // Decoded before the slot is removed, so a decode that raises leaves the value as it was.
            nb::object value = types.decode_member(node_, 1, copy.data(), label_);
            // Removed only while lookup still finds the same slot holding the bytes decoded; when another
            // thread changed it meanwhile, the pop starts again, so two threads never pop the same item.
            nb::ft_lock_guard guard(mutex_);
            if (locate(encoded.data(), scanning) == at && std::memcmp(slot(at), copy.data(), stride_) == 0) {
                remove_at(at, encoded.data(), scanning);
                return value;
            }
        }
    }
    if (fallback.is_valid())
        return nb::borrow(fallback);
    missing_key(key);
}

nb::object NativeDict::popitem() {
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
            raise(PyExc_KeyError, "popitem(): dictionary is empty");
        // Decoded before the slot is removed, so a decode that raises leaves the value as it was.
        nb::object key = types.decode_member(node_, 0, copy.data(), label_);
        nb::object value = types.decode_member(node_, 1, copy.data(), label_);
        // Removed only while the last slot still holds the bytes decoded, as pop does.
        nb::ft_lock_guard guard(mutex_);
        if (count_ == at + 1 && std::memcmp(slot(at), copy.data(), stride_) == 0) {
            remove_moving_last(at);
            return nb::make_tuple(key, value);
        }
    }
}

nb::object NativeDict::setdefault(nb::handle key, nb::handle fallback) {
    const Types &types = codec();
    SlotBuffer item(stride_);
    encode_key(key, item.data(), false);
    bool encoded = false;
    for (;;) {
        {
            nb::ft_lock_guard guard(mutex_);
            if (const std::uint32_t at = lookup(item.data()); at != no_slot) {
                std::memcpy(item.data(), slot(at), stride_);
                break;
            }
            if (encoded) {
                // Duplicates another program stored may fill the capacity; dropping them may make room.
                if (count_ == capacity_)
                    ensure_checked();
                if (count_ < capacity_) {
                    append_slot(item.data());
                    return nb::borrow(fallback);
                }
            }
        }
        if (encoded)
            too_many(std::size_t{capacity_} + 1);
        // Encoded only for a key the value does not hold, so a fallback of another type raises only then, as
        // dict.setdefault ignores it otherwise; the key is looked up again, since another thread may have
        // added it meanwhile.
        types.encode_member(node_, 1, fallback.ptr(), item.data(), label_);
        encoded = true;
    }
    return types.decode_member(node_, 1, item.data(), label_);
}

void NativeDict::items_of(nb::handle other, std::vector<std::byte> &out) const {
    if (nb::isinstance<NativeDict>(other)) {
        NativeDict &theirs = *nb::inst_ptr<NativeDict>(other);
        if (theirs.has_elements(codec().element_text(node_), codec().element_annotation(node_))) {
            nb::ft_lock_guard guard(theirs.mutex_);
            theirs.ensure_checked();
            out.insert(out.end(), theirs.slot(0), theirs.slot(theirs.count_));
            return;
        }
    }
    if (PyObject_HasAttrString(other.ptr(), "keys")) {
        for (nb::handle key : other.attr("keys")())
            encode_item(key, other[key], out);
        return;
    }
    std::size_t n = 0;
    for (nb::handle element : other) {
        const nb::object pair = nb::steal(PySequence_Tuple(element.ptr()));
        if (!pair.is_valid())
            throw nb::python_error();
        if (const Py_ssize_t length = PyTuple_Size(pair.ptr()); length != 2)
            raise(PyExc_ValueError, "dictionary update sequence element #" + std::to_string(n) + " has length " +
                                        std::to_string(length) + "; 2 is required");
        encode_item(PyTuple_GetItem(pair.ptr(), 0), PyTuple_GetItem(pair.ptr(), 1), out);
        ++n;
    }
}

std::size_t NativeDict::put_all(const std::vector<std::byte> &items) {
    ensure_checked();
    const std::uint32_t before = count_;
    // New keys are appended first, in the order they first appear, so a result past the capacity is undone by
    // restoring the count; then each item sets its value, so a key's last item wins.
    for (std::size_t at = 0; at < items.size(); at += stride_) {
        const std::byte *item = items.data() + at;
        if (find(item) != no_slot)
            continue;
        if (count_ == capacity_) {
            forget();
            set_count(before);
            return std::size_t{capacity_} + 1;
        }
        append_slot(item);
    }
    for (std::size_t at = 0; at < items.size(); at += stride_) {
        const std::byte *item = items.data() + at;
        std::memcpy(slot(find(item)) + value_offset_, item + value_offset_, stride_ - value_offset_);
    }
    return 0;
}

void NativeDict::update(nb::handle other, nb::kwargs pairs) {
    std::vector<std::byte> items;
    if (other.is_valid()) {
        refuse_unbuilt(other);
        items_of(other, items);
    }
    if (pairs.size() != 0)
        items_of(pairs, items);
    std::size_t refused = 0;
    {
        nb::ft_lock_guard guard(mutex_);
        refused = put_all(items);
    }
    if (refused != 0)
        too_many(refused);
}

void NativeDict::remove_all() {
    nb::ft_lock_guard guard(mutex_);
    forget();
    splice(0, count_, nullptr, 0);
    checked_ = true;
}

nb::object NativeDict::merge(nb::pointer_and_handle<NativeDict> self, nb::handle other, bool reflected) {
    const NativeDict &d = *self.p;
    d.refuse_unbuilt(other);
    if (!instance(other.ptr(), scalars::mapping()))
        return nb::borrow(Py_NotImplemented);
    std::vector<std::byte> items;
    d.items_of(other, items);
    nb::object out;
    if (reflected) {
        // Other's items first, then self's, which win for a key both hold.
        d.items_of(self.h, items);
        std::vector<std::byte> empty;
        {
            nb::ft_lock_guard guard(d.mutex_);
            empty = d.header(0);
        }
        out = like(self, empty);
    } else {
        out = copy(self);
    }
    NativeDict &result = *nb::inst_ptr<NativeDict>(out);
    std::size_t refused = 0;
    {
        nb::ft_lock_guard guard(result.mutex_);
        refused = result.put_all(items);
    }
    if (refused != 0)
        d.too_many(refused);
    return out;
}

} // namespace sharedbox
