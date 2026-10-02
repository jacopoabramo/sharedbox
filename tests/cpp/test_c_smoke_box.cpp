// The C++ half of test_c_smoke.c: creates and removes the box the C program opens.
#include "table_builder.hpp"
#include "unique.hpp"

#include <optional>

namespace {

std::string box_name;
std::optional<sharedbox::handle> box;

} // namespace

extern "C" const char *smoke_create(void) {
    // An enum of two members, the description of field 3.
    static table_builder types = [] {
        table_builder t;
        t.head(sharedbox::kind_enum, 2, 2);
        t.name("OFF");
        t.name("ON");
        t.pad();
        return t;
    }();
    const sharedbox::field_spec fields[4] = {{0, 8, sharedbox::kind_int},
                                             {8, 16, sharedbox::kind_datetime},
                                             {24, 16, sharedbox::kind_complex},
                                             {40, 0, sharedbox::kind_enum}};
    box_name = unique("c-smoke");
    auto made = sharedbox::handle::create(box_name, fields, 48, 0x5EED, 4, {}, types.span());
    if (!made)
        return nullptr;
    box.emplace(std::move(*made));
    return box_name.c_str();
}

extern "C" void smoke_remove(void) {
    box.reset();
    static_cast<void>(sharedbox::unlink(box_name));
}
