#include "collection.hpp"

#include <algorithm>
#include <cstring>
#include <new>
#include <utility>

namespace sharedbox {

namespace {

/// The bytes of one slot, on the stack when they fit.
class SlotBuffer {
public:
    explicit SlotBuffer(std::size_t size) : heap_(size > sizeof local_ ? new std::byte[size] : nullptr) {}
    std::byte *data() { return heap_ ? heap_.get() : local_; }

private:
    alignas(std::max_align_t) std::byte local_[64];
    std::unique_ptr<std::byte[]> heap_;
};

/// An integer index; error, when given, is raised for one past the range of Py_ssize_t, which is clamped
/// otherwise. One that is not an integer raises TypeError.
Py_ssize_t as_index(nb::handle index, PyObject *error) {
    const Py_ssize_t i = PyNumber_AsSsize_t(index.ptr(), error);
    if (i == -1 && PyErr_Occurred())
        throw nb::python_error();
    return i;
}

/// value compared with element as list compares them, element first.
bool same(const nb::object &element, nb::handle value) {
    const int equal = PyObject_RichCompareBool(element.ptr(), value.ptr(), Py_EQ);
    if (equal < 0)
        throw nb::python_error();
    return equal != 0;
}

} // namespace

NativeCollection::NativeCollection(nb::object types, const detail::type_ref &list, std::string label,
                                   std::span<const std::byte> stored)
    : types_(std::move(types)), codec_(&nb::cast<const Types &>(types_)), list_(list), label_(std::move(label)) {
    const detail::type_node &node = codec_->node_of(list_);
    slots_ = node.slots;
    stride_ = node.stride;
    capacity_ = node.capacity;
    if (stored.size() >= 4)
        std::memcpy(&count_, stored.data(), 4);
    const std::size_t used = slots_ + std::size_t{count_} * stride_;
    if (stored.size() < 4 || count_ > node.capacity || stored.size() < used)
        corrupt(label_);
    room_ = count_;
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

std::pair<NativeCollection::Slice, std::size_t> NativeCollection::fit(Slice slice) const {
    const Py_ssize_t length =
        PySlice_AdjustIndices(static_cast<Py_ssize_t>(count_), &slice.start, &slice.stop, slice.step);
    return {slice, static_cast<std::size_t>(length)};
}

void NativeCollection::set_count(std::size_t count) {
    count_ = static_cast<std::uint32_t>(count);
    std::memcpy(data_.get(), &count_, 4);
}

void NativeCollection::out_of_range(Py_ssize_t index) const {
    raise(PyExc_IndexError, label_ + ": index " + std::to_string(index) + " is out of range for " +
                                std::to_string(size()) + " elements");
}

void NativeCollection::too_many(std::size_t length) const {
    raise(PyExc_ValueError, label_ + " holds at most " + std::to_string(capacity_) +
                                " elements; the value would have " + std::to_string(length));
}

void NativeCollection::too_many(std::size_t count, Py_ssize_t times) const {
    const nb::object length = nb::steal(PyNumber_Multiply(nb::int_(count).ptr(), nb::int_(times).ptr()));
    if (!length.is_valid())
        throw nb::python_error();
    raise(PyExc_ValueError, label_ + " holds at most " + std::to_string(capacity_) +
                                " elements; the value would have " + nb::str(length).c_str());
}

nb::object NativeCollection::element(Py_ssize_t index) const {
    // Decoded from a copy taken under the mutex, because decoding may run Python code, which could
    // change this value while the decode reads it.
    // ponytail: copies the whole slot, so a str element of a large capacity costs its capacity per read.
    SlotBuffer copy(stride_);
    {
        nb::ft_lock_guard guard(mutex_);
        const std::size_t at = position(index);
        if (at == no_position)
            return {};
        std::memcpy(copy.data(), slot(at), stride_);
    }
    return codec().decode_element(list_, copy.data(), label_);
}

NativeCollection::Slice NativeCollection::unpack(nb::handle slice) {
    Slice s;
    if (PySlice_Unpack(slice.ptr(), &s.start, &s.stop, &s.step) < 0)
        throw nb::python_error();
    return s;
}

nb::object NativeCollection::getitem(nb::pointer_and_handle<NativeCollection> self, nb::handle index) {
    const NativeCollection &c = *self.p;
    if (!PySlice_Check(index.ptr())) {
        const Py_ssize_t i = as_index(index, PyExc_IndexError);
        nb::object value = c.element(i);
        if (!value.is_valid())
            c.out_of_range(i);
        return value;
    }
    const Slice asked = unpack(index);
    std::vector<std::byte> part;
    {
        nb::ft_lock_guard guard(c.mutex_);
        const auto [s, length] = c.fit(asked);
        part = c.header(length);
        part.resize(c.slots_ + length * c.stride_);
        std::byte *out = part.data() + c.slots_;
        if (s.step == 1)
            std::memcpy(out, c.slot(static_cast<std::size_t>(s.start)), length * c.stride_);
        else
            for (std::size_t k = 0; k < length; ++k)
                std::memcpy(out + k * c.stride_,
                            c.slot(static_cast<std::size_t>(s.start + Py_ssize_t(k) * s.step)), c.stride_);
    }
    return like(self, part);
}

std::vector<std::byte> NativeCollection::header(std::size_t count) const {
    std::vector<std::byte> out(data_.get(), data_.get() + slots_);
    const auto stored = static_cast<std::uint32_t>(count);
    std::memcpy(out.data(), &stored, 4);
    return out;
}

nb::object NativeCollection::like(nb::pointer_and_handle<NativeCollection> self,
                                  std::span<const std::byte> stored) {
    const NativeCollection &c = *self.p;
    // Raises for a value the collector cleared, before its empty types_ is copied into the new value.
    c.codec();
    // Built with the mutex released, because allocating the instance may run the collector.
    return make(nb::handle(reinterpret_cast<PyObject *>(Py_TYPE(self.h.ptr()))), c.types_, c.list_, c.label_,
                stored);
}

nb::object NativeCollection::repeated(nb::pointer_and_handle<NativeCollection> self, Py_ssize_t times) {
    const NativeCollection &c = *self.p;
    std::vector<std::byte> part;
    std::size_t count = 0;
    bool refused = false;
    {
        nb::ft_lock_guard guard(c.mutex_);
        count = c.count_;
        // An empty value repeated any number of times stays empty, so it is never looped over.
        if (times < 1 || count == 0) {
            part = c.header(0);
        } else if (c.too_many_times(count, times)) {
            refused = true;
        } else {
            const std::size_t used = count * c.stride_;
            part = c.header(count * static_cast<std::size_t>(times));
            part.resize(c.slots_ + used * static_cast<std::size_t>(times));
            for (Py_ssize_t k = 0; k < times; ++k)
                std::memcpy(part.data() + c.slots_ + static_cast<std::size_t>(k) * used, c.slot(0), used);
        }
    }
    if (refused)
        c.too_many(count, times);
    return like(self, part);
}

nb::object NativeCollection::copy(nb::pointer_and_handle<NativeCollection> self) { return repeated(self, 1); }

nb::object NativeCollection::add(nb::pointer_and_handle<NativeCollection> self, nb::handle other) {
    if (!PyList_Check(other.ptr()) && !(nb::isinstance<NativeCollection>(other) && nb::inst_ready(other)))
        return nb::borrow(Py_NotImplemented);
    nb::object out = copy(self);
    nb::inst_ptr<NativeCollection>(out)->extend(other);
    return out;
}

nb::object NativeCollection::multiply(nb::pointer_and_handle<NativeCollection> self, nb::handle times) {
    if (!PyIndex_Check(times.ptr()))
        return nb::borrow(Py_NotImplemented);
    return repeated(self, as_index(times, PyExc_OverflowError));
}

void NativeCollection::repeat(nb::handle times) {
    const Py_ssize_t n = as_index(times, PyExc_OverflowError);
    std::size_t count = 0;
    bool refused = false;
    {
        nb::ft_lock_guard guard(mutex_);
        count = count_;
        if (n < 1) {
            splice(0, count_, nullptr, 0);
        } else if (too_many_times(count, n)) {
            refused = true;
        } else if (n > 1 && count != 0) {
            // Copied aside, because growing the buffer moves the slots the copies are taken from.
            const std::size_t used = count * stride_;
            std::vector<std::byte> copies(used * static_cast<std::size_t>(n - 1));
            for (Py_ssize_t k = 0; k + 1 < n; ++k)
                std::memcpy(copies.data() + static_cast<std::size_t>(k) * used, slot(0), used);
            splice(count_, count_, copies.data(), count * static_cast<std::size_t>(n - 1));
        }
    }
    if (refused)
        too_many(count, n);
}

nb::object NativeCollection::element_annotation() const { return nb::borrow(codec().element_annotation(list_)); }

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
    SlotBuffer encoded(stride_);
    std::memset(encoded.data(), 0, stride_);
    codec().encode_element(list_, value.ptr(), encoded.data(), label_);
    std::size_t at = no_position;
    {
        nb::ft_lock_guard guard(mutex_);
        at = position(index);
        if (at != no_position)
            std::memcpy(slot(at), encoded.data(), stride_);
    }
    if (at == no_position)
        out_of_range(index);
}

void NativeCollection::setitem(nb::handle index, nb::handle value) {
    if (!PySlice_Check(index.ptr()))
        return set(as_index(index, PyExc_IndexError), value);
    const Slice asked = unpack(index);
    if (asked.step != 1)
        return set_stepped(asked, value);
    std::size_t base = 0;
    {
        nb::ft_lock_guard guard(mutex_);
        const auto [s, length] = fit(asked);
        base = count_ - length;
    }
    const std::vector<std::byte> encoded = encode_all(value, base);
    std::size_t refused = 0;
    {
        // Fitted again, since another thread may have changed the count while the elements encoded.
        nb::ft_lock_guard guard(mutex_);
        const auto [s, length] = fit(asked);
        const auto start = static_cast<std::size_t>(s.start);
        refused = splice(start, start + length, encoded.data(), encoded.size() / stride_);
    }
    if (refused != 0)
        too_many(refused);
}

void NativeCollection::set_stepped(const Slice &asked, nb::handle value) {
    // Taken whole first, as list takes it, so the value may be this one and its length is known.
    const nb::object items = nb::steal(PySequence_List(value.ptr()));
    if (!items.is_valid())
        throw nb::python_error();
    const auto given = static_cast<std::size_t>(PyList_Size(items.ptr()));
    const auto mismatch = [given](std::size_t length) {
        raise(PyExc_ValueError, "attempt to assign sequence of size " + std::to_string(given) +
                                    " to extended slice of size " + std::to_string(length));
    };
    std::size_t length = 0;
    {
        nb::ft_lock_guard guard(mutex_);
        length = fit(asked).second;
    }
    if (given != length)
        mismatch(length);
    // No more elements than the value already holds, so the capacity cannot refuse them.
    const std::vector<std::byte> encoded = encode_all(items, 0);
    {
        nb::ft_lock_guard guard(mutex_);
        const auto [s, now] = fit(asked);
        length = now;
        if (length == given)
            for (std::size_t k = 0; k < length; ++k)
                std::memcpy(slot(static_cast<std::size_t>(s.start + Py_ssize_t(k) * s.step)),
                            encoded.data() + k * stride_, stride_);
    }
    if (length != given)
        mismatch(length);
}

void NativeCollection::delitem(nb::handle index) {
    if (!PySlice_Check(index.ptr())) {
        const Py_ssize_t i = as_index(index, PyExc_IndexError);
        std::size_t at = no_position;
        {
            nb::ft_lock_guard guard(mutex_);
            at = position(i);
            if (at != no_position)
                splice(at, at + 1, nullptr, 0);
        }
        if (at == no_position)
            out_of_range(i);
        return;
    }
    const Slice asked = unpack(index);
    nb::ft_lock_guard guard(mutex_);
    auto [s, length] = fit(asked);
    if (length == 0)
        return;
    if (s.step < 0) {
        s.start += Py_ssize_t(length - 1) * s.step;
        s.step = -s.step;
    }
    const auto first = static_cast<std::size_t>(s.start);
    const auto step = static_cast<std::size_t>(s.step);
    if (step == 1) {
        splice(first, first + length, nullptr, 0);
        return;
    }
    std::size_t kept = first;
    for (std::size_t read = first; read < count_; ++read) {
        if ((read - first) % step == 0 && (read - first) / step < length)
            continue;
        std::memmove(slot(kept++), slot(read), stride_);
    }
    set_count(count_ - length);
}

void NativeCollection::insert_at(Py_ssize_t index, nb::handle value) {
    SlotBuffer encoded(stride_);
    std::memset(encoded.data(), 0, stride_);
    codec().encode_element(list_, value.ptr(), encoded.data(), label_);
    std::size_t refused = 0;
    {
        nb::ft_lock_guard guard(mutex_);
        const auto count = static_cast<Py_ssize_t>(count_);
        const auto at =
            static_cast<std::size_t>(std::clamp(index < 0 ? index + count : index, Py_ssize_t{0}, count));
        refused = splice(at, at, encoded.data(), 1);
    }
    if (refused != 0)
        too_many(refused);
}

void NativeCollection::insert(nb::handle index, nb::handle value) {
    insert_at(as_index(index, PyExc_OverflowError), value);
}

void NativeCollection::append(nb::handle value) { insert_at(PY_SSIZE_T_MAX, value); }

void NativeCollection::extend(nb::handle iterable) {
    const std::vector<std::byte> encoded = encode_all(iterable, size());
    std::size_t refused = 0;
    {
        nb::ft_lock_guard guard(mutex_);
        refused = splice(count_, count_, encoded.data(), encoded.size() / stride_);
    }
    if (refused != 0)
        too_many(refused);
}

nb::object NativeCollection::pop(nb::handle index) {
    const Py_ssize_t i = as_index(index, PyExc_OverflowError);
    const Types &types = codec();
    SlotBuffer copy(stride_);
    for (;;) {
        bool empty = false;
        std::size_t at = no_position;
        {
            nb::ft_lock_guard guard(mutex_);
            empty = count_ == 0;
            at = position(i);
            if (at != no_position)
                std::memcpy(copy.data(), slot(at), stride_);
        }
        if (empty)
            raise(PyExc_IndexError, "pop from empty list");
        if (at == no_position)
            raise(PyExc_IndexError, "pop index out of range");
        // Decoded before the slot is removed, so a decode that raises leaves the value as it was.
        nb::object value = types.decode_element(list_, copy.data(), label_);
        bool removed = false;
        {
            // Removed only while the slot still holds the bytes decoded; when another thread changed it
            // meanwhile, the pop starts again, so two threads never pop the same element.
            nb::ft_lock_guard guard(mutex_);
            if (position(i) == at && std::memcmp(slot(at), copy.data(), stride_) == 0) {
                splice(at, at + 1, nullptr, 0);
                removed = true;
            }
        }
        if (removed)
            return value;
    }
}

Py_ssize_t NativeCollection::find(nb::handle value, Py_ssize_t start, Py_ssize_t stop) const {
    for (Py_ssize_t i = start; i < stop; ++i) {
        const nb::object mine = element(i);
        if (!mine.is_valid())
            break;
        if (same(mine, value))
            return i;
    }
    return -1;
}

void NativeCollection::remove(nb::handle value) {
    const Py_ssize_t at = find(value, 0, PY_SSIZE_T_MAX);
    if (at < 0)
        raise(PyExc_ValueError, "list.remove(x): x not in list");
    nb::ft_lock_guard guard(mutex_);
    if (position(at) != no_position)
        splice(static_cast<std::size_t>(at), static_cast<std::size_t>(at) + 1, nullptr, 0);
}

Py_ssize_t NativeCollection::index(nb::handle value, nb::handle start, nb::handle stop) const {
    const auto count = static_cast<Py_ssize_t>(size());
    const auto clamp = [count](Py_ssize_t i) { return i < 0 ? std::max<Py_ssize_t>(i + count, 0) : i; };
    const Py_ssize_t at = find(value, clamp(as_index(start, nullptr)), clamp(as_index(stop, nullptr)));
    if (at < 0)
        raise(PyExc_ValueError, "list.index(x): x not in list");
    return at;
}

std::size_t NativeCollection::count(nb::handle value) const {
    std::size_t found = 0;
    for (Py_ssize_t i = 0;; ++i) {
        const nb::object mine = element(i);
        if (!mine.is_valid())
            return found;
        found += same(mine, value) ? 1 : 0;
    }
}

bool NativeCollection::contains(nb::handle value) const { return find(value, 0, PY_SSIZE_T_MAX) >= 0; }

void NativeCollection::reverse() {
    nb::ft_lock_guard guard(mutex_);
    for (std::size_t i = 0, j = count_; i + 1 < j; ++i, --j)
        std::swap_ranges(slot(i), slot(i) + stride_, slot(j - 1));
}

void NativeCollection::remove_all() {
    nb::ft_lock_guard guard(mutex_);
    splice(0, count_, nullptr, 0);
}

std::vector<std::byte> NativeCollection::encode_all(nb::handle iterable, std::size_t base) const {
    const Types &types = codec();
    std::vector<std::byte> encoded;
    const Py_ssize_t sized = PyObject_Size(iterable.ptr());
    if (sized >= 0) {
        if (base + static_cast<std::size_t>(sized) > capacity_)
            too_many(base + static_cast<std::size_t>(sized));
        encoded.reserve(static_cast<std::size_t>(sized) * stride_);
    } else if (PyErr_ExceptionMatches(PyExc_TypeError)) {
        PyErr_Clear();
    } else {
        throw nb::python_error();
    }
    const nb::object items = nb::steal(PyObject_GetIter(iterable.ptr()));
    if (!items.is_valid())
        throw nb::python_error();
    for (std::size_t n = base;; ++n) {
        const nb::object item = nb::steal(PyIter_Next(items.ptr()));
        if (!item.is_valid())
            break;
        if (n >= capacity_)
            too_many(n + 1);
        const std::size_t at = encoded.size();
        encoded.resize(at + stride_);
        types.encode_element(list_, item.ptr(), encoded.data() + at, label_);
    }
    if (PyErr_Occurred())
        throw nb::python_error();
    return encoded;
}

std::size_t NativeCollection::splice(std::size_t start, std::size_t stop, const std::byte *encoded,
                                     std::size_t n) {
    const std::size_t count = count_ - (stop - start) + n;
    if (count > capacity_)
        return count;
    if (count > room_) {
        const auto room = static_cast<std::uint32_t>(
            std::min<std::size_t>(capacity_, std::max<std::size_t>(count, std::size_t{room_} * 2)));
        std::unique_ptr<std::byte[]> grown(new std::byte[slots_ + std::size_t{room} * stride_]);
        std::memcpy(grown.get(), data_.get(), slots_ + std::size_t{count_} * stride_);
        data_ = std::move(grown);
        room_ = room;
    }
    std::memmove(slot(start + n), slot(stop), (count_ - stop) * stride_);
    if (n != 0)
        std::memcpy(slot(start), encoded, n * stride_);
    set_count(count);
    return 0;
}

nb::object NativeCollection::equals(nb::handle other) const {
    const bool list = PyList_Check(other.ptr());
    if (!list && !(nb::isinstance<NativeCollection>(other) && nb::inst_ready(other)))
        return nb::borrow(Py_NotImplemented);
    const NativeCollection *collection = list ? nullptr : nb::inst_ptr<NativeCollection>(other);
    const Py_ssize_t length = list ? PyObject_Size(other.ptr()) : static_cast<Py_ssize_t>(collection->size());
    if (length < 0)
        throw nb::python_error();
    if (static_cast<std::size_t>(length) != size())
        return nb::bool_(false);
    for (Py_ssize_t i = 0; i < length; ++i) {
        const nb::object mine = element(i);
        const nb::object theirs = list ? nb::steal(PySequence_GetItem(other.ptr(), i)) : collection->element(i);
        if (list && !theirs.is_valid()) {
            if (!PyErr_ExceptionMatches(PyExc_IndexError))
                throw nb::python_error();
            PyErr_Clear();
        }
        // An element missing on either side means a comparison or another thread shortened that value.
        if (!mine.is_valid() || !theirs.is_valid() || !same(mine, theirs))
            return nb::bool_(false);
    }
    return nb::bool_(true);
}

nb::bytes NativeCollection::stored() const {
    nb::ft_lock_guard guard(mutex_);
    return nb::bytes(data_.get(), slots_ + std::size_t{count_} * stride_);
}

bool NativeCollection::has_elements(const std::string &text, PyObject *annotation) const {
    const Types &types = codec();
    if (types.element_text(list_) != text)
        return false;
    const int equal = PyObject_RichCompareBool(types.element_annotation(list_), annotation, Py_EQ);
    if (equal < 0)
        throw nb::python_error();
    return equal != 0;
}

std::size_t NativeCollection::copy_to(std::byte *out, std::uint32_t capacity, const std::string &name) const {
    nb::ft_lock_guard guard(mutex_);
    if (count_ > capacity)
        raise(PyExc_ValueError, name + " holds at most " + std::to_string(capacity) + " elements; the value has " +
                                    std::to_string(count_));
    const std::size_t size = slots_ + std::size_t{count_} * stride_;
    std::memcpy(out, data_.get(), size);
    return size;
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
