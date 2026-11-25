cmake_minimum_required(VERSION 3.20)
project(sharedbox_consumer LANGUAGES C CXX)

find_package(sharedbox ${SHAREDBOX_VERSION} CONFIG REQUIRED)

add_library(consumer SHARED consumer.c stream_consumer.cpp)
target_link_libraries(consumer PRIVATE sharedbox::c sharedbox::headers)
set_target_properties(consumer PROPERTIES C_STANDARD 99 CXX_STANDARD 20 C_VISIBILITY_PRESET hidden
    CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
