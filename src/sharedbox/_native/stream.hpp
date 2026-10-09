#pragma once

#include <nanobind/nanobind.h>

namespace sharedbox {

/// Adds Stream, Sender, Reader and the stream errors to the module.
void bind_stream(nanobind::module_ &m);

} // namespace sharedbox
