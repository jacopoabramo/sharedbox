cmake_minimum_required(VERSION 3.30.3)
project(capsule_consumer LANGUAGES CXX)

# Accept a free-threaded interpreter too. From CMake 3.30.3 Python::Module then defines Py_GIL_DISABLED,
# which the Windows pyconfig.h leaves out.
set(Python_FIND_ABI "ANY;ANY;ANY;ANY")
find_package(Python 3.11 REQUIRED
    COMPONENTS Interpreter Development.Module
    OPTIONAL_COMPONENTS Development.SABIModule)
execute_process(
    COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
    OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT)
find_package(nanobind CONFIG REQUIRED)
# scikit-build-core puts the build environment's site-packages on CMAKE_PREFIX_PATH, where the sharedbox
# wheel keeps its CMake config.
find_package(sharedbox CONFIG REQUIRED)

nanobind_add_module(_producer STABLE_ABI FREE_THREADED src/capsule_consumer/_producer.cpp)
target_link_libraries(_producer PRIVATE sharedbox::headers)
target_compile_features(_producer PRIVATE cxx_std_20)
if(WIN32)
    target_compile_definitions(_producer PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
endif()

install(TARGETS _producer LIBRARY DESTINATION capsule_consumer)
