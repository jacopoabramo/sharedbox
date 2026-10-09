// Run only in a container whose /dev/shm is 16 MiB, as the cpp_header CI job does: a box larger than
// that must fail at create with ENOSPC, rather than kill a later writer with SIGBUS.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include "unique.hpp"

#include <cerrno>
#include <vector>

TEST_CASE("a box larger than /dev/shm fails at create and leaves the name free") {
    constexpr std::uint32_t count = 32;
    constexpr std::uint32_t span = 4 + sharedbox::max_capacity;
    std::vector<sharedbox::field_spec> fields;
    for (std::uint32_t i = 0; i < count; ++i)
        fields.push_back({i * span, sharedbox::max_capacity, sharedbox::kind_bytes});
    const std::string name = unique("small-shm");
    const auto large = sharedbox::handle::create(name, fields, count * span, 1, 64, {});
    CHECK((!large && large.error().code == sharedbox::status::os && large.error().os == ENOSPC));
    // The failed create left the name free.
    const auto small = sharedbox::handle::create(name, {fields.data(), 1}, span, 1, 64, {});
    CHECK(small.has_value());
    static_cast<void>(sharedbox::unlink(name));
}
