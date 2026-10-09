// core.hpp compiles on its own, so a later stream.hpp can include it without box.hpp.
#include <sharedbox/core.hpp>

#include <doctest/doctest.h>

TEST_CASE("core.hpp stands alone") {
    CHECK(sharedbox::detail::current_pid() != 0);
    const sharedbox::result<int> r = 1;
    CHECK(*r == 1);
}
