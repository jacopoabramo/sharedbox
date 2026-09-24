#include <nanobind/nanobind.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>

#include "segment.hpp"

namespace nb = nanobind;
using namespace nb::literals;
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

    nb::class_<Segment>(m, "Segment")
        .def_static(
            "create",
            [](const std::string &name, const std::vector<std::tuple<std::uint64_t, std::uint32_t, bool>> &fields,
               std::uint64_t record_size, std::uint64_t schema_hash, double lock_timeout) {
                std::vector<sharedbox::FieldDesc> descs;
                descs.reserve(fields.size());
                for (const auto &[offset, capacity, prefixed] : fields)
                    descs.push_back({offset, capacity,
                                     prefixed ? sharedbox::FieldKind::Prefixed : sharedbox::FieldKind::Fixed});
                return Segment::create(name, descs, record_size, schema_hash, lock_timeout);
            },
            "name"_a, "fields"_a, "record_size"_a, "schema_hash"_a, "lock_timeout"_a)
        .def_static("attach", &Segment::attach, "name"_a, "schema_hash"_a, "lock_timeout"_a)
        .def("read", [](const Segment &s, std::uint32_t field) { return to_bytes(s.read(field)); }, "field"_a)
        .def("read_all",
             [](const Segment &s) -> nb::typed<nb::list, nb::bytes> {
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
        .def("wait", &Segment::wait, "last_generation"_a, "timeout"_a, nb::call_guard<nb::gil_scoped_release>())
        .def("force_unlock", &Segment::force_unlock)
        .def("_hold_write_lock", &Segment::hold_write_lock)
        .def("close", &Segment::close)
        .def_static("unlink", &Segment::unlink, "name"_a)
        .def_prop_ro("closed", &Segment::closed)
        .def_prop_ro("name", &Segment::name);
}
