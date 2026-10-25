#include "collection.hpp"

#include <cstring>
#include <new>
#include <utility>

namespace sharedbox {

NativeCollection::NativeCollection(nb::object types, const detail::type_ref &list, std::string label,
                                   std::span<const std::byte> stored)
    : types_(std::move(types)), codec_(&nb::cast<const Types &>(types_)), list_(list), label_(std::move(label)) {
    const detail::type_node &node = codec_->node_of(list_);
    slots_ = node.slots;
    stride_ = node.stride;
    if (stored.size() >= 4)
        std::memcpy(&count_, stored.data(), 4);
    const std::size_t used = slots_ + std::size_t{count_} * stride_;
    if (stored.size() < 4 || count_ > node.capacity || stored.size() < used)
        corrupt(label_);
    data_.reset(new std::byte[used]);
    if (used >= detail::large_copy) {
        nb::gil_scoped_release unlocked;
        std::memcpy(data_.get(), stored.data(), used);
    } else {
        std::memcpy(data_.get(), stored.data(), used);
    }
}

nb::object NativeCollection::make(nb::handle cls, nb::object types, const detail::type_ref &list,
                                  std::string label, std::span<const std::byte> stored) {
    nb::object self = nb::inst_alloc(cls);
    // An instance left unready by a constructor that throws is freed without running the destructor.
    new (nb::inst_ptr<NativeCollection>(self)) NativeCollection(std::move(types), list, std::move(label), stored);
    nb::inst_mark_ready(self);
    return self;
}

const Types &NativeCollection::codec() const {
    // tp_clear empties types_ only on a value the collector is about to free.
    if (codec_ == nullptr)
        raise(PyExc_ValueError, label_ + ": the value has been cleared");
    return *codec_;
}

std::size_t NativeCollection::size() const {
    nb::ft_lock_guard guard(mutex_);
    return count_;
}

std::size_t NativeCollection::position(Py_ssize_t index) const {
    const auto count = static_cast<Py_ssize_t>(count_);
    const Py_ssize_t at = index < 0 ? index + count : index;
    if (at < 0 || at >= count)
        return no_position;
    return static_cast<std::size_t>(at);
}

void NativeCollection::out_of_range(Py_ssize_t index) const {
    raise(PyExc_IndexError, label_ + ": index " + std::to_string(index) + " is out of range for " +
                                std::to_string(size()) + " elements");
}

nb::object NativeCollection::element(Py_ssize_t index) const {
    // Decoded from a copy taken under the mutex, because decoding may run Python code, which could
    // change this value while the decode reads it.
    // ponytail: copies the whole slot, so a str element of a large capacity costs its capacity per read.
    alignas(std::max_align_t) std::byte local[64];
    std::unique_ptr<std::byte[]> heap(stride_ > sizeof local ? new std::byte[stride_] : nullptr);
    std::byte *copy = heap ? heap.get() : local;
    {
        nb::ft_lock_guard guard(mutex_);
        const std::size_t at = position(index);
        if (at == no_position)
            return {};
        std::memcpy(copy, data_.get() + slots_ + at * stride_, stride_);
    }
    return codec().decode_element(list_, copy, label_);
}

nb::object NativeCollection::get(Py_ssize_t index) const {
    nb::object value = element(index);
    if (!value.is_valid())
        out_of_range(index);
    return value;
}

nb::object NativeCollection::next(std::size_t index) const { return element(static_cast<Py_ssize_t>(index)); }

void NativeCollection::set(Py_ssize_t index, nb::handle value) {
    bool inside = false;
    {
        nb::ft_lock_guard guard(mutex_);
        inside = position(index) != no_position;
    }
    if (!inside)
        out_of_range(index);
    // Encoded aside, so a value that does not fit leaves the element as it was, and Python code the encode
    // runs cannot change the slot while it is written.
    std::unique_ptr<std::byte[]> encoded(new std::byte[stride_]());
    codec().encode_element(list_, value.ptr(), encoded.get(), label_);
    std::size_t at = no_position;
    {
        nb::ft_lock_guard guard(mutex_);
        at = position(index);
        if (at != no_position)
            std::memcpy(data_.get() + slots_ + at * stride_, encoded.get(), stride_);
    }
    if (at == no_position)
        out_of_range(index);
}

nb::bytes NativeCollection::stored() const {
    nb::ft_lock_guard guard(mutex_);
    return nb::bytes(data_.get(), slots_ + std::size_t{count_} * stride_);
}

int NativeCollection::traverse(visitproc visit, void *arg) const {
    Py_VISIT(types_.ptr());
    return 0;
}

void NativeCollection::clear() {
    codec_ = nullptr;
    // Moved out and dropped on return, so code the decref runs finds this value already cleared.
    nb::object types = std::move(types_);
}

} // namespace sharedbox
