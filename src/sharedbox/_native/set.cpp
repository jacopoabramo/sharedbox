#include "set.hpp"

#include <vector>

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

} // namespace sharedbox
