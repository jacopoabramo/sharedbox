# sharedbox::c adds sharedbox_c.cpp to the sources of whatever links it, and CMake silently skips a source
# whose language is not enabled, which leaves every sbx_ function undefined at link time. So once the
# top-level project has been read, a target linking sharedbox::c in a build without CXX stops the configure.
# A target that reaches sharedbox::c only through another library is not seen.
function(_sharedbox_require_cxx)
    get_property(languages GLOBAL PROPERTY ENABLED_LANGUAGES)
    if("CXX" IN_LIST languages)
        return()
    endif()
    set(directories "${CMAKE_SOURCE_DIR}")
    while(directories)
        list(POP_FRONT directories directory)
        get_property(targets DIRECTORY "${directory}" PROPERTY BUILDSYSTEM_TARGETS)
        foreach(target IN LISTS targets)
            get_target_property(libraries ${target} LINK_LIBRARIES)
            get_target_property(interface ${target} INTERFACE_LINK_LIBRARIES)
            if("${libraries};${interface}" MATCHES "(^|;)sharedbox::c(;|::@|$)")
                message(FATAL_ERROR "${target} links sharedbox::c, whose sharedbox_c.cpp is C++: add CXX to "
                                    "the project's LANGUAGES or call enable_language(CXX)")
            endif()
        endforeach()
        get_property(subdirectories DIRECTORY "${directory}" PROPERTY SUBDIRECTORIES)
        list(APPEND directories ${subdirectories})
    endwhile()
endfunction()

get_property(_sharedbox_deferred GLOBAL PROPERTY _sharedbox_require_cxx_deferred)
if(NOT _sharedbox_deferred)
    set_property(GLOBAL PROPERTY _sharedbox_require_cxx_deferred TRUE)
    cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}" CALL _sharedbox_require_cxx)
endif()
unset(_sharedbox_deferred)
