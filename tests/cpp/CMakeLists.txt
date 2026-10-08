cmake_minimum_required(VERSION 3.25)
project(sharedbox_cpp_tests LANGUAGES C CXX)
enable_testing()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Threads REQUIRED)

include(FetchContent)
FetchContent_Declare(doctest
    URL https://github.com/doctest/doctest/archive/refs/tags/v2.4.12.tar.gz
    URL_HASH SHA256=73381c7aa4dee704bd935609668cf41880ea7f19fa0504a200e13b74999c2d70
    SYSTEM)
FetchContent_MakeAvailable(doctest)

# This repository, added the way a project using FetchContent adds it.
add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../.." sharedbox)

# The libraries and warnings every test target builds with.
function(sbx_test_target target)
    target_link_libraries(${target} PRIVATE sharedbox::headers)
    if(MSVC)
        target_compile_options(${target} PRIVATE /W4 /WX)
    else()
        target_compile_options(${target} PRIVATE -Wall -Wextra -Werror)
    endif()
endfunction()

set(sbx_tests test_result test_atomics test_mapping test_lock test_slots test_wait test_layout2 test_types test_values test_write_in_place test_core_alone)
if(WIN32)
    list(APPEND sbx_tests test_windows_h)
else()
    # fork() and pid namespaces exist only on POSIX.
    list(APPEND sbx_tests test_fork)
endif()
foreach(target IN LISTS sbx_tests)
    add_executable(${target} ${target}.cpp)
endforeach()

# The same test again where the standard library has std::expected, to check the conversions.
if("cxx_std_23" IN_LIST CMAKE_CXX_COMPILE_FEATURES)
    add_executable(test_result_cxx23 test_result.cpp)
    set_target_properties(test_result_cxx23 PROPERTIES CXX_STANDARD 23)
    list(APPEND sbx_tests test_result_cxx23)
    include(CheckCXXSourceCompiles)
    set(CMAKE_CXX_STANDARD 23)
    check_cxx_source_compiles([=[
        #include <expected>
        #if !defined(__cpp_lib_expected) || __cpp_lib_expected < 202211L
        #error no monadic std::expected
        #endif
        int main() { return 0; }
    ]=] SBX_HAS_STD_EXPECTED)
    set(CMAKE_CXX_STANDARD 20)
    if(SBX_HAS_STD_EXPECTED)
        target_compile_definitions(test_result_cxx23 PRIVATE SBX_EXPECT_STD_EXPECTED)
    endif()
endif()

foreach(target IN LISTS sbx_tests)
    target_link_libraries(${target} PRIVATE doctest::doctest_with_main)
endforeach()

# A plain C program with its own exit code, since doctest is C++.
add_executable(test_c_smoke test_c_smoke.c test_c_smoke_box.cpp)
set_target_properties(test_c_smoke PROPERTIES C_STANDARD 99 C_STANDARD_REQUIRED ON)
target_link_libraries(test_c_smoke PRIVATE sharedbox::c)
list(APPEND sbx_tests test_c_smoke)

foreach(target IN LISTS sbx_tests)
    sbx_test_target(${target})
    add_test(NAME ${target} COMMAND ${target})
    set_tests_properties(${target} PROPERTIES TIMEOUT 60)
endforeach()

# Needs a 16 MiB /dev/shm, so CI runs it in a container instead of through CTest.
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_executable(test_small_shm test_small_shm.cpp)
    sbx_test_target(test_small_shm)
    target_link_libraries(test_small_shm PRIVATE doctest::doctest_with_main)
endif()

# Each case builds compile_fail.cpp with one call that must not compile and passes on its static_assert
# message. The C++20 result only: std::expected words its own messages.
set(sbx_compile_fail
    "or_else_type=or_else must return the same result type"
    "transform_reference=transform must not return a reference"
    "value_or_explicit=value_or needs a value convertible to T")
foreach(entry IN LISTS sbx_compile_fail)
    string(REPLACE "=" ";" parts "${entry}")
    list(GET parts 0 case)
    list(GET parts 1 message)
    add_executable(fail_${case} EXCLUDE_FROM_ALL compile_fail.cpp)
    set_target_properties(fail_${case} PROPERTIES EXCLUDE_FROM_DEFAULT_BUILD ON)
    target_link_libraries(fail_${case} PRIVATE sharedbox::headers)
    target_compile_definitions(fail_${case} PRIVATE SBX_FAIL_${case})
    add_test(NAME fail_${case} COMMAND ${CMAKE_COMMAND} --build ${CMAKE_BINARY_DIR} --target fail_${case} --config $<CONFIG>)
    set_tests_properties(fail_${case} PROPERTIES PASS_REGULAR_EXPRESSION "${message}" TIMEOUT 120 RESOURCE_LOCK sbx_build)
endforeach()
