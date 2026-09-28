// Box names for the tests in this folder.
#pragma once

#include <sharedbox/sharedbox.hpp>

#include <string>

// A box name no other test process uses.
inline std::string unique(const char *tag) {
    return "sbtest-cpp-" + std::string(tag) + "-" + std::to_string(sharedbox::detail::current_pid());
}
