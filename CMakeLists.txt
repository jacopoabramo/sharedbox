cmake_minimum_required(VERSION 3.15)
project(${SKBUILD_PROJECT_NAME} LANGUAGES CXX C)

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(
  Python
  COMPONENTS Interpreter Development.Module
  REQUIRED)

# Find Boost
find_package(Boost COMPONENTS system filesystem)
if(NOT Boost_FOUND)
    # Fallback for systems where Boost is not easily found
    if(WIN32)
        set(VCPKG_ROOT $ENV{VCPKG_ROOT})
        if(NOT VCPKG_ROOT)
            set(VCPKG_ROOT "C:/vcpkg")
        endif()
        set(Boost_INCLUDE_DIRS "${VCPKG_ROOT}/installed/x64-windows/include")
        set(Boost_LIBRARY_DIRS "${VCPKG_ROOT}/installed/x64-windows/lib" "${VCPKG_ROOT}/installed/x64-windows/bin")
    else()
        set(Boost_INCLUDE_DIRS "/usr/include" "/usr/local/include" "/usr/include/boost")
        set(Boost_LIBRARY_DIRS "/usr/lib" "/usr/local/lib")
    endif()
endif()

add_custom_command(
  OUTPUT src/sharedbox/_shareddict.cpp
  COMMENT
    "Making ${CMAKE_CURRENT_BINARY_DIR}/src/sharedbox/_shareddict.cpp from ${CMAKE_CURRENT_SOURCE_DIR}/src/sharedbox/_shareddict.pyx"
  COMMAND Python::Interpreter -m cython
          "${CMAKE_CURRENT_SOURCE_DIR}/src/sharedbox/_shareddict.pyx" --output-file src/sharedbox/_shareddict.cpp --cplus
  DEPENDS src/sharedbox/_shareddict.pyx
  VERBATIM
)

add_custom_command(
    OUTPUT src/sharedbox/utils.cpp
    COMMENT
      "Making ${CMAKE_CURRENT_BINARY_DIR}/src/sharedbox/utils.cpp from ${CMAKE_CURRENT_SOURCE_DIR}/src/sharedbox/utils.py"
    COMMAND Python::Interpreter -m cython
            "${CMAKE_CURRENT_SOURCE_DIR}/src/sharedbox/utils.py" --output-file src/sharedbox/utils.cpp --cplus
    DEPENDS src/sharedbox/utils.py
  VERBATIM
)

# Platform-specific compiler settings
if(WIN32)
    set(PLATFORM_COMPILE_DEFS
        BOOST_ALL_NO_LIB
        _WIN32_WINNT=0x0601
        WIN32_LEAN_AND_MEAN
        NOMINMAX
    )
    set(PLATFORM_COMPILE_ARGS /std:c++17 /EHsc /O2)
    set(PLATFORM_LIBRARIES kernel32 user32 advapi32)
else()
    set(PLATFORM_COMPILE_DEFS
        BOOST_ALL_NO_LIB
        _GNU_SOURCE
    )
    set(PLATFORM_COMPILE_ARGS -std=c++17 -O3)
    set(PLATFORM_LIBRARIES rt pthread)
endif()

# Create the _shareddict extension
python_add_library(_shareddict MODULE 
    src/sharedbox/_shareddict.cpp 
    src/sharedbox/_core/shared_dict.cpp
    WITH_SOABI)

target_include_directories(_shareddict PRIVATE 
    src/sharedbox/_core
    ${Boost_INCLUDE_DIRS}
)

target_compile_definitions(_shareddict PRIVATE ${PLATFORM_COMPILE_DEFS})
target_compile_options(_shareddict PRIVATE ${PLATFORM_COMPILE_ARGS})

if(Boost_LIBRARY_DIRS)
    target_link_directories(_shareddict PRIVATE ${Boost_LIBRARY_DIRS})
endif()

target_link_libraries(_shareddict PRIVATE ${PLATFORM_LIBRARIES})

# Create the utils extension
python_add_library(utils MODULE 
    src/sharedbox/utils.cpp
    WITH_SOABI)

target_compile_definitions(utils PRIVATE ${PLATFORM_COMPILE_DEFS})
target_compile_options(utils PRIVATE ${PLATFORM_COMPILE_ARGS})

# Install both extensions to the sharedbox package
install(TARGETS _shareddict DESTINATION sharedbox)
install(TARGETS utils DESTINATION sharedbox)