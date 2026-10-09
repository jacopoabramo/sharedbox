// sharedbox.hpp: every sharedbox segment kind, for consumers that want one include.
//
// C++20, header-only, 64-bit little-endian targets, no exceptions. Names in sharedbox::detail are
// internal and may change in any release.
#ifndef SHAREDBOX_SHAREDBOX_HPP
#define SHAREDBOX_SHAREDBOX_HPP

#include <sharedbox/box.hpp>
#include <sharedbox/stream.hpp>

#undef SHAREDBOX_HOT
#ifdef SHAREDBOX_DEFINED_NOMINMAX
#undef NOMINMAX
#undef SHAREDBOX_DEFINED_NOMINMAX
#endif
#ifdef SHAREDBOX_DEFINED_WIN32_LEAN_AND_MEAN
#undef WIN32_LEAN_AND_MEAN
#undef SHAREDBOX_DEFINED_WIN32_LEAN_AND_MEAN
#endif

#endif
