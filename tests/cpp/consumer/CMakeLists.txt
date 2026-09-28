cmake_minimum_required(VERSION 3.20)
project(sharedbox_consumer LANGUAGES C CXX)

find_package(sharedbox ${SHAREDBOX_VERSION} CONFIG REQUIRED)

add_library(consumer SHARED consumer.c)
target_link_libraries(consumer PRIVATE sharedbox::c)
set_target_properties(consumer PROPERTIES C_STANDARD 99 C_VISIBILITY_PRESET hidden CXX_VISIBILITY_PRESET hidden)
