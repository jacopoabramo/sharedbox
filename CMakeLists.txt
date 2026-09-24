cmake_minimum_required(VERSION 3.30...4.4)

if(NOT DEFINED ENV{VCPKG_ROOT})
    message(FATAL_ERROR "Set VCPKG_ROOT to a vcpkg checkout.")
endif()
set(CMAKE_TOOLCHAIN_FILE "$ENV{VCPKG_ROOT}/scripts/buildsystems/vcpkg.cmake"
    CACHE STRING "vcpkg toolchain file")

project(${SKBUILD_PROJECT_NAME} LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Python 3.11 REQUIRED
    COMPONENTS Interpreter Development.Module
    OPTIONAL_COMPONENTS Development.SABIModule)
execute_process(
    COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
    OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT)
find_package(nanobind CONFIG REQUIRED)
find_package(Boost REQUIRED CONFIG)
find_package(Threads REQUIRED)

nanobind_add_module(_shareddict
    STABLE_ABI
    FREE_THREADED
    NB_DOMAIN sharedbox
    src/sharedbox/shareddict.cpp
    src/sharedbox/_core/sharedmemory.cpp)
target_include_directories(_shareddict PRIVATE src/sharedbox src/sharedbox/_core)
target_link_libraries(_shareddict PRIVATE Boost::headers)

if(WIN32)
    target_compile_definitions(_shareddict PRIVATE
        BOOST_ALL_NO_LIB WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
else()
    target_link_libraries(_shareddict PRIVATE rt Threads::Threads)
endif()

nanobind_add_stub(_shareddict_stub
    MODULE _shareddict
    OUTPUT ${CMAKE_CURRENT_SOURCE_DIR}/src/sharedbox/_shareddict.pyi
    PYTHON_PATH $<TARGET_FILE_DIR:_shareddict>
    DEPENDS _shareddict
    MARKER_FILE ${CMAKE_CURRENT_SOURCE_DIR}/src/sharedbox/py.typed)

install(TARGETS _shareddict LIBRARY DESTINATION sharedbox)
install(FILES
    ${CMAKE_CURRENT_SOURCE_DIR}/src/sharedbox/_shareddict.pyi
    ${CMAKE_CURRENT_SOURCE_DIR}/src/sharedbox/py.typed
    DESTINATION sharedbox)
