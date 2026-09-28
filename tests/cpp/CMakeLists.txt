cmake_minimum_required(VERSION 3.25)
project(sharedbox_cpp_tests LANGUAGES CXX)
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

# The include path, libraries and warnings every test target builds with.
function(sbx_test_target target)
    target_include_directories(${target} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../../include")
    target_link_libraries(${target} PRIVATE doctest::doctest_with_main Threads::Threads)
    if(MSVC)
        target_compile_options(${target} PRIVATE /W4 /WX)
    else()
        target_compile_options(${target} PRIVATE -Wall -Wextra -Werror)
        target_link_libraries(${target} PRIVATE rt)
    endif()
endfunction()

set(sbx_tests test_result test_atomics test_mapping test_lock test_slots test_wait)
if(NOT WIN32)
    # fork() and pid namespaces exist only on POSIX.
    list(APPEND sbx_tests test_fork)
endif()
foreach(target IN LISTS sbx_tests)
    add_executable(${target} ${target}.cpp)
endforeach()

# The same test again where the standard library has std::expected, which result then names.
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
    sbx_test_target(${target})
    add_test(NAME ${target} COMMAND ${target})
    set_tests_properties(${target} PROPERTIES TIMEOUT 60)
endforeach()

# Needs a 16 MiB /dev/shm, so CI runs it in a container instead of through CTest.
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_executable(test_small_shm test_small_shm.cpp)
    sbx_test_target(test_small_shm)
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
    target_include_directories(fail_${case} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../../include")
    target_compile_definitions(fail_${case} PRIVATE SBX_FAIL_${case})
    add_test(NAME fail_${case} COMMAND ${CMAKE_COMMAND} --build ${CMAKE_BINARY_DIR} --target fail_${case} --config $<CONFIG>)
    set_tests_properties(fail_${case} PROPERTIES PASS_REGULAR_EXPRESSION "${message}" TIMEOUT 120)
endforeach()
