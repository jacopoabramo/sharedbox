# find_package(sharedbox) from an installed sharedbox wheel: add the folder above this package's
# include folder, Path(sharedbox.get_include()).parent, to CMAKE_PREFIX_PATH.
get_filename_component(_sharedbox_include "${CMAKE_CURRENT_LIST_DIR}/../../../include" ABSOLUTE)
if(NOT TARGET sharedbox::headers)
    add_library(sharedbox::headers INTERFACE IMPORTED)
    set_target_properties(sharedbox::headers PROPERTIES
        INTERFACE_INCLUDE_DIRECTORIES "${_sharedbox_include}"
        INTERFACE_COMPILE_FEATURES cxx_std_20)
    if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
        include(CMakeFindDependencyMacro)
        find_dependency(Threads)
        set_property(TARGET sharedbox::headers APPEND PROPERTY INTERFACE_LINK_LIBRARIES rt Threads::Threads)
    endif()
    # sharedbox_c.cpp is compiled into whatever links this, which needs CXX enabled; the included file checks.
    add_library(sharedbox::c INTERFACE IMPORTED)
    set_target_properties(sharedbox::c PROPERTIES
        INTERFACE_SOURCES "${_sharedbox_include}/sharedbox/sharedbox_c.cpp"
        INTERFACE_LINK_LIBRARIES sharedbox::headers)
endif()
unset(_sharedbox_include)
include("${CMAKE_CURRENT_LIST_DIR}/sharedbox-require-cxx.cmake")
