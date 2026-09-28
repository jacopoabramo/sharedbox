// The C++ half of test_c_smoke.c: creates and removes the box the C program opens.
#include "unique.hpp"

#include <optional>

namespace {

std::string box_name;
std::optional<sharedbox::handle> box;

} // namespace

extern "C" const char *smoke_create(void) {
    constexpr sharedbox::field_spec fields[1] = {{0, 8, sharedbox::kind_int}};
    box_name = unique("c-smoke");
    auto made = sharedbox::handle::create(box_name, fields, 8, 0x5EED, 4, {});
    if (!made)
        return nullptr;
    box.emplace(std::move(*made));
    return box_name.c_str();
}

extern "C" void smoke_remove(void) {
    box.reset();
    static_cast<void>(sharedbox::unlink(box_name));
}
