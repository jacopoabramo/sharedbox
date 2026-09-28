cmake_minimum_required(VERSION 3.30...4.4)

project(sharedbox LANGUAGES NONE)

if(SKBUILD)
    enable_language(CXX)
endif()

# The headers on their own, for projects that add this repository with FetchContent.
add_library(sharedbox_headers INTERFACE)
add_library(sharedbox::headers ALIAS sharedbox_headers)
target_include_directories(sharedbox_headers INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_compile_features(sharedbox_headers INTERFACE cxx_std_20)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    find_package(Threads REQUIRED)
    target_link_libraries(sharedbox_headers INTERFACE rt Threads::Threads)
endif()

# The C interface: sharedbox_c.cpp is compiled into whatever links this, which needs CXX enabled.
add_library(sharedbox_c INTERFACE)
add_library(sharedbox::c ALIAS sharedbox_c)
target_sources(sharedbox_c INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/include/sharedbox/sharedbox_c.cpp")
target_link_libraries(sharedbox_c INTERFACE sharedbox::headers)

if(NOT SKBUILD)
    return()
endif()

find_package(Python 3.11 REQUIRED
    COMPONENTS Interpreter Development.Module
    OPTIONAL_COMPONENTS Development.SABIModule)
execute_process(
    COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
    OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT)
find_package(nanobind CONFIG REQUIRED)

nanobind_add_module(_native
    STABLE_ABI
    FREE_THREADED
    LTO
    NB_DOMAIN sharedbox
    src/sharedbox/_native/codec.cpp
    src/sharedbox/_native/module.cpp
    src/sharedbox/_native/segment.cpp)
target_link_libraries(_native PRIVATE sharedbox::headers)

if(WIN32)
    target_compile_definitions(_native PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
endif()

install(TARGETS _native LIBRARY DESTINATION sharedbox)
install(DIRECTORY include/ DESTINATION sharedbox/include)
install(FILES cmake/sharedbox-config.cmake DESTINATION sharedbox/share/cmake/sharedbox)
