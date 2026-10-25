#include "set.hpp"

#include <vector>

#include "types.hpp"

namespace sharedbox {

nb::object NativeSet::copy(nb::pointer_and_handle<NativeSet> self) {
    const NativeSet &s = *self.p;
    std::vector<std::byte> stored;
    {
        nb::ft_lock_guard guard(s.mutex_);
        stored.assign(s.data_.get(), s.data_.get() + s.slots_ + std::size_t{s.count_} * s.stride_);
    }
    return like(self, stored);
}

} // namespace sharedbox
