cmake_minimum_required(VERSION 3.15)

# Set vcpkg toolchain from VCPKG_ROOT environment variable
if(DEFINED ENV{VCPKG_ROOT})
    set(CMAKE_TOOLCHAIN_FILE "$ENV{VCPKG_ROOT}/scripts/buildsystems/vcpkg.cmake"
        CACHE STRING "Vcpkg toolchain file")
    message(STATUS "Using vcpkg toolchain from VCPKG_ROOT: ${CMAKE_TOOLCHAIN_FILE}")
else()
    message(FATAL_ERROR "VCPKG_ROOT environment variable is not set. Please install vcpkg and set VCPKG_ROOT to the vcpkg installation directory.")
endif()

project(${SKBUILD_PROJECT_NAME} LANGUAGES CXX C)

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if (CMAKE_VERSION VERSION_LESS 3.18)
  set(DEV_MODULE Development)
else()
  set(DEV_MODULE Development.Module)
endif()

set(Python_ROOT_DIR "${CMAKE_SOURCE_DIR}/.venv")
find_package(Python 3.10 COMPONENTS Interpreter ${DEV_MODULE} REQUIRED)

message(STATUS "Using Python interpreter: ${Python_EXECUTABLE}")

if (NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Choose the type of build." FORCE)
  set_property(CACHE CMAKE_BUILD_TYPE PROPERTY STRINGS "Debug" "Release" "MinSizeRel" "RelWithDebInfo")
endif()

execute_process(
  COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
  OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT
)
find_package(nanobind CONFIG REQUIRED)

find_package(Boost REQUIRED CONFIG)

message(STATUS "Found Boost version: ${Boost_VERSION}")

# Platform-specific compiler settings
if(WIN32)
    set(PLATFORM_COMPILE_DEFS
        BOOST_ALL_NO_LIB
        _WIN32_WINNT=0x0601
        WIN32_LEAN_AND_MEAN
        NOMINMAX
    )
    set(PLATFORM_COMPILE_ARGS /EHsc /O2)
    set(PLATFORM_LIBRARIES kernel32 user32 advapi32)
else()
    set(PLATFORM_COMPILE_DEFS
        BOOST_ALL_NO_LIB
        _GNU_SOURCE
    )
    set(PLATFORM_COMPILE_ARGS -O3)
    set(PLATFORM_LIBRARIES rt pthread)
endif()

# Create the _shareddict extension using nanobind
nanobind_add_module(_shareddict
    NB_STATIC  # Link nanobind statically
    src/sharedbox/shareddict.cpp
    src/sharedbox/_core/sharedmemory.cpp
)

target_include_directories(_shareddict PRIVATE 
    src/sharedbox
    src/sharedbox/_core
)

target_compile_definitions(_shareddict PRIVATE ${PLATFORM_COMPILE_DEFS})
target_compile_options(_shareddict PRIVATE ${PLATFORM_COMPILE_ARGS})

# Link Boost (header-only, no library needed for interprocess/container) and platform libraries
target_link_libraries(_shareddict PRIVATE 
    Boost::headers  # Boost header-only libraries
    ${PLATFORM_LIBRARIES}
)

# Generate stub file for the _shareddict module in the source folder
nanobind_add_stub(
    _shareddict_stub
    MODULE _shareddict
    OUTPUT ${CMAKE_CURRENT_SOURCE_DIR}/src/sharedbox/_shareddict.pyi
    PYTHON_PATH $<TARGET_FILE_DIR:_shareddict>
    DEPENDS _shareddict
    MARKER_FILE ${CMAKE_CURRENT_SOURCE_DIR}/src/sharedbox/py.typed
)

# Install the extension to the sharedbox package
install(TARGETS _shareddict LIBRARY DESTINATION sharedbox)

# Install stub file and marker file from source folder
install(FILES 
    ${CMAKE_CURRENT_SOURCE_DIR}/src/sharedbox/_shareddict.pyi
    ${CMAKE_CURRENT_SOURCE_DIR}/src/sharedbox/py.typed
    DESTINATION sharedbox
)