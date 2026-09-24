#include <nanobind/nanobind.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>

#include "segment.hpp"

namespace nb = nanobind;
using namespace nb::literals;
using sharedbox::FieldDesc;
using sharedbox::FieldKind;
using sharedbox::Segment;

namespace {

nb::bytes to_bytes(const std::string &value) { return nb::bytes(value.data(), value.size()); }

} // namespace

NB_MODULE(_native, m) {
    nb::exception<sharedbox::SegmentExists>(m, "SegmentExistsError", PyExc_FileExistsError);
    nb::exception<sharedbox::SegmentMissing>(m, "SegmentNotFoundError", PyExc_FileNotFoundError);
    nb::exception<sharedbox::SchemaMismatch>(m, "SchemaMismatchError", PyExc_TypeError);
    nb::exception<sharedbox::SegmentClosed>(m, "BoxClosedError", PyExc_ValueError);
    nb::exception<sharedbox::LockTimeout>(m, "LockTimeoutError", PyExc_TimeoutError);

    nb::enum_<FieldKind>(m, "FieldKind")
        .value("FIXED", FieldKind::Fixed)
        .value("PREFIXED", FieldKind::Prefixed);

    nb::class_<FieldDesc>(m, "FieldDesc")
        .def(
            "__init__",
            [](FieldDesc *self, std::uint64_t offset, std::uint32_t capacity, FieldKind kind) {
                new (self) FieldDesc{offset, capacity, kind};
            },
            "offset"_a, "capacity"_a, "kind"_a)
        .def_ro("offset", &FieldDesc::offset)
        .def_ro("capacity", &FieldDesc::capacity)
        .def_ro("kind", &FieldDesc::kind);

    nb::class_<Segment>(m, "Segment")
        .def_static("create", &Segment::create, "name"_a, "fields"_a, "record_size"_a, "schema_hash"_a,
                    "lock_timeout"_a)
        .def_static("attach", &Segment::attach, "name"_a, "schema_hash"_a, "lock_timeout"_a)
        .def("read", [](const Segment &s, std::uint32_t field) { return to_bytes(s.read(field)); }, "field"_a)
        .def("read_all",
             [](const Segment &s) {
                 std::vector<std::string> values = s.read_all();
                 nb::list_builder out(values.size());
                 for (const auto &value : values)
                     out.put(to_bytes(value));
                 return out.commit();
             })
        .def(
            "write",
            [](Segment &s, const std::vector<std::pair<std::uint32_t, nb::bytes>> &values) {
                std::vector<std::pair<std::uint32_t, std::string>> copied;
                copied.reserve(values.size());
                for (const auto &[index, data] : values)
                    copied.emplace_back(index, std::string(data.c_str(), data.size()));
                s.write(copied);
            },
            "values"_a)
        .def("version", &Segment::version, "field"_a)
        .def("generation", &Segment::generation)
        .def("force_unlock", &Segment::force_unlock)
        .def("_hold_write_lock", &Segment::hold_write_lock)
        .def("close", &Segment::close)
        .def_static("unlink", &Segment::unlink, "name"_a)
        .def_prop_ro("closed", &Segment::closed)
        .def_prop_ro("name", &Segment::name);
}
