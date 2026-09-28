// Compiles sharedbox.hpp after the full windows.h, whose min and max macros are then defined.
#include <windows.h>

#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#ifndef min
#error "windows.h was expected to define min"
#endif
