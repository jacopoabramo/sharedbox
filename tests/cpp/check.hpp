// CHECK for the tests in this folder: a failed check prints its line and the test keeps going, so one
// run reports every failure; finish() gives the exit code CTest reads.
#pragma once

#include <sharedbox/sharedbox.hpp>

#include <cstdio>
#include <string>

inline int failures = 0;

#define CHECK(cond)                                                                                               \
    do {                                                                                                          \
        if (!(cond)) {                                                                                            \
            std::fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #cond);                                       \
            ++failures;                                                                                           \
        }                                                                                                         \
    } while (0)

inline int finish(const char *test) {
    if (failures == 0)
        std::printf("%s: ok\n", test);
    return failures == 0 ? 0 : 1;
}
