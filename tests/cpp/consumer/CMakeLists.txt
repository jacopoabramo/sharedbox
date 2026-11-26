cmake_minimum_required(VERSION 3.30)
project(sharedbox_consumer LANGUAGES C CXX)

find_package(sharedbox ${SHAREDBOX_VERSION} CONFIG REQUIRED)
# Accept a free-threaded interpreter too; Python_EXECUTABLE decides which build is used.
set(Python_FIND_ABI "ANY;ANY;ANY;ANY")
find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)

add_library(consumer SHARED consumer.c stream_consumer.cpp)
target_link_libraries(consumer PRIVATE sharedbox::c sharedbox::headers)
set_target_properties(consumer PROPERTIES C_STANDARD 99 CXX_STANDARD 20 C_VISIBILITY_PRESET hidden
    CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)

add_library(pyconsumer SHARED python_consumer.cpp)
target_link_libraries(pyconsumer PRIVATE sharedbox::headers Python::Module)
set_target_properties(pyconsumer PROPERTIES CXX_STANDARD 20 CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
