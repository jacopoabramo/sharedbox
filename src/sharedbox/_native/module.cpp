#include <nanobind/nanobind.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>

#include <system_error>

#include "codec.hpp"
#include "liveness.hpp"
#include "segment.hpp"

namespace nb = nanobind;
using namespace nb::literals;
using sharedbox::Segment;

namespace {

using Values = std::vector<std::pair<std::uint32_t, nb::object>>;
using Encoded = std::vector<std::pair<std::uint32_t, std::string>>;

nb::bytes to_bytes(const std::string &value) { return nb::bytes(value.data(), value.size()); }

sharedbox::FieldKind to_kind(std::uint32_t code) {
    if (!sharedbox::kind_is_valid(code))
        throw std::invalid_argument("unknown field kind " + std::to_string(code));
    return static_cast<sharedbox::FieldKind>(code);
}

nb::object decode_value(const sharedbox::FieldDesc &field, const std::string &bytes) {
    PyObject *value = sharedbox::decode(field, bytes.data(), bytes.size());
    if (value == nullptr)
        throw nb::python_error();
    return nb::steal(value);
}

} // namespace

NB_MODULE(_native, m) {
    nb::exception<sharedbox::SegmentExists>(m, "SegmentExistsError", PyExc_FileExistsError);
    nb::exception<sharedbox::SegmentMissing>(m, "SegmentNotFoundError", PyExc_FileNotFoundError);
    nb::exception<sharedbox::SchemaMismatch>(m, "SchemaMismatchError", PyExc_TypeError);
    nb::exception<sharedbox::SegmentClosed>(m, "BoxClosedError", PyExc_ValueError);
    nb::exception<sharedbox::LockTimeout>(m, "LockTimeoutError", PyExc_TimeoutError);
    nb::register_exception_translator([](const std::exception_ptr &p, void *) {
        try {
            std::rethrow_exception(p);
        } catch (const std::system_error &e) {
            // Only errno values map onto OSError's errno; other categories fall through to RuntimeError.
            if (e.code().category() != std::generic_category())
                throw;
            nb::object error = nb::steal(PyObject_CallFunction(PyExc_OSError, "is", e.code().value(), e.what()));
            if (error.is_valid())
                PyErr_SetObject(PyExc_OSError, error.ptr());
        }
    });
    sharedbox::set_wait_hooks([]() -> void * { return PyEval_SaveThread(); },
                              [](void *state) { PyEval_RestoreThread(static_cast<PyThreadState *>(state)); });

    m.def(
        "check",
        [](std::uint32_t kind, std::uint32_t capacity, const std::string &name, nb::handle value) {
            sharedbox::encode({0, capacity, to_kind(kind)}, name, value.ptr());
        },
        "kind"_a, "capacity"_a, "name"_a, "value"_a);

    m.def("_process_start", &sharedbox::process_start, "pid"_a);
    m.def(
        "_process_alive",
        [](std::uint32_t pid, std::uint64_t start) { return sharedbox::process_alive({pid, start}); }, "pid"_a,
        "start"_a);

    nb::class_<Segment>(m, "Segment")
        .def_static(
            "create",
            [](const std::string &name,
               const std::vector<std::tuple<std::uint32_t, std::uint32_t, std::uint32_t>> &fields,
               const std::vector<std::string> &names, std::uint64_t record_size, std::uint64_t schema_hash,
               double lock_timeout, const Values &values) {
                std::vector<sharedbox::FieldDesc> descs;
                descs.reserve(fields.size());
                for (const auto &[offset, capacity, kind] : fields)
                    descs.push_back({offset, capacity, to_kind(kind)});
                if (names.size() != descs.size())
                    throw std::invalid_argument("got " + std::to_string(names.size()) + " field names for " +
                                                std::to_string(descs.size()) + " fields");
                Encoded encoded;
                encoded.reserve(values.size());
                for (const auto &[index, value] : values) {
                    if (index >= descs.size())
                        throw std::out_of_range("field index " + std::to_string(index) + " is out of range");
                    encoded.emplace_back(index, sharedbox::encode(descs[index], names[index], value.ptr()));
                }
                return Segment::create(name, descs, names, record_size, schema_hash, lock_timeout, encoded);
            },
            "name"_a, "fields"_a, "names"_a, "record_size"_a, "schema_hash"_a, "lock_timeout"_a, "values"_a)
        .def_static("attach", &Segment::attach, "name"_a, "names"_a, "schema_hash"_a, "lock_timeout"_a)
        .def(
            "get",
            [](const Segment &s, std::uint32_t index) { return decode_value(s.field(index), s.read(index)); },
            "field"_a)
        .def(
            "get_versioned",
            [](const Segment &s, std::uint32_t index) {
                const sharedbox::FieldDesc &f = s.field(index);
                auto [version, bytes] = s.read_versioned(index);
                return nb::make_tuple(version, decode_value(f, bytes));
            },
            "field"_a)
        .def("get_all",
             [](const Segment &s) -> nb::list {
                 std::vector<std::string> values = s.read_all();
                 nb::list out;
                 for (std::uint32_t i = 0; i < values.size(); ++i)
                     out.append(decode_value(s.field(i), values[i]));
                 return out;
             })
        .def(
            "set",
            [](Segment &s, const Values &values) {
                Encoded encoded;
                encoded.reserve(values.size());
                for (const auto &[index, value] : values)
                    encoded.emplace_back(index,
                                         sharedbox::encode(s.field(index), s.field_name(index), value.ptr()));
                s.write(encoded);
            },
            "values"_a)
        .def(
            "_read", [](const Segment &s, std::uint32_t field) { return to_bytes(s.read(field)); }, "field"_a)
        .def("_read_all",
             [](const Segment &s) -> nb::typed<nb::list, nb::bytes> {
                 std::vector<std::string> values = s.read_all();
                 nb::list_builder out(values.size());
                 for (const auto &value : values)
                     out.put(to_bytes(value));
                 return out.commit();
             })
        .def(
            "_write",
            [](Segment &s, const std::vector<std::pair<std::uint32_t, nb::bytes>> &values) {
                Encoded copied;
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
        .def("_after_fork", &Segment::after_fork)
        // A read blocked on another writer's lock releases the GIL and needs it back before it
        // lets close() through, so close() must not hold the GIL while it waits.
        .def("close", &Segment::close, nb::call_guard<nb::gil_scoped_release>())
        .def_static("unlink", &Segment::unlink, "name"_a)
        .def_prop_ro("_size", &Segment::size)
        .def_prop_ro("closed", &Segment::closed)
        .def_prop_ro("name", &Segment::name)
        .def_prop_ro("lock_timeout", &Segment::lock_timeout);
}
