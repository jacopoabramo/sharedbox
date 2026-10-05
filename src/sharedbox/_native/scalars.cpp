#include "scalars.hpp"

#include <nanobind/nanobind.h>
#include <sharedbox/sharedbox.hpp>

#ifndef Py_LIMITED_API
#include <datetime.h>
#endif

#include <array>
#include <atomic>
#include <cstring>
#include <optional>

namespace nb = nanobind;

namespace sharedbox::scalars {
namespace {

// Set by init() and kept for the life of the process.
PyObject *date_type = nullptr;
PyObject *time_type = nullptr;
PyObject *datetime_type = nullptr;
PyObject *timedelta_type = nullptr;
PyObject *timezone_type = nullptr;
PyObject *uuid_type = nullptr;
PyObject *decimal_type = nullptr;
PyObject *mapping_abc = nullptr;
PyObject *sequence_abc = nullptr;
PyObject *set_abc = nullptr;
PyObject *utcoffset_name = nullptr;
PyObject *bytes_name = nullptr;
PyObject *fold_name = nullptr;
PyObject *tzinfo_name = nullptr;
PyObject *empty_tuple = nullptr;

// One timezone per offset in minutes, made on first use and never freed. Two threads may both make one;
// the one whose swap fails drops its own.
std::array<std::atomic<PyObject *>, 2 * max_offset_minutes + 1> zones{};

[[noreturn]] void raise(PyObject *type, const std::string &message) {
    PyErr_SetString(type, message.c_str());
    throw nb::python_error();
}

PyObject *owned(PyObject *made) {
    if (made == nullptr)
        throw nb::python_error();
    return made;
}

const char *kind_name(std::uint8_t kind) {
    switch (kind) {
    case kind_complex:
        return "a complex";
    case kind_date:
        return "a date";
    case kind_time:
        return "a time";
    case kind_datetime:
        return "a datetime";
    case kind_timedelta:
        return "a timedelta";
    case kind_uuid:
        return "a UUID";
    default:
        return "a Decimal";
    }
}

[[noreturn]] void wrong_type(std::uint8_t kind, PyObject *value, const std::string &name) {
    const nb::object type_name = nb::handle(reinterpret_cast<PyObject *>(Py_TYPE(value))).attr("__name__");
    raise(PyExc_TypeError,
          name + " expects " + kind_name(kind) + ", got " + nb::borrow<nb::str>(type_name).c_str());
}

bool instance(PyObject *value, PyObject *type) {
    const int found = PyObject_IsInstance(value, type);
    if (found < 0)
        throw nb::python_error();
    return found == 1;
}

// Days from 1970-01-01 to y-m-d in the proleptic Gregorian calendar.
constexpr std::int64_t days_from_civil(std::int64_t y, unsigned m, unsigned d) noexcept {
    y -= m <= 2 ? 1 : 0;
    const std::int64_t era = (y >= 0 ? y : y - 399) / 400;
    const auto yoe = static_cast<unsigned>(y - era * 400);
    const unsigned doy = (153 * (m > 2 ? m - 3 : m + 9) + 2) / 5 + d - 1;
    const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + static_cast<std::int64_t>(doe) - 719468;
}

struct civil {
    int year;
    int month;
    int day;
};

constexpr civil civil_from_days(std::int64_t z) noexcept {
    z += 719468;
    const std::int64_t era = (z >= 0 ? z : z - 146096) / 146097;
    const auto doe = static_cast<unsigned>(z - era * 146097);
    const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    const std::int64_t y = static_cast<std::int64_t>(yoe) + era * 400;
    const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const unsigned mp = (5 * doy + 2) / 153;
    const unsigned d = doy - (153 * mp + 2) / 5 + 1;
    const unsigned m = mp < 10 ? mp + 3 : mp - 9;
    return {static_cast<int>(y + (m <= 2 ? 1 : 0)), static_cast<int>(m), static_cast<int>(d)};
}

static_assert(days_from_civil(1970, 1, 1) == 0);
static_assert(days_from_civil(1, 1, 1) == 1 - unix_epoch_ordinal);
static_assert(civil_from_days(max_date_ordinal - unix_epoch_ordinal).year == 9999);

struct clock_fields {
    int year, month, day, hour, minute, second, microsecond, fold;
};

#ifdef Py_LIMITED_API
PyObject *names[10];
enum { year_i, month_i, day_i, hour_i, minute_i, second_i, microsecond_i, days_i, seconds_i, microseconds_i };

int int_attr(PyObject *value, int which) {
    const nb::object got = nb::steal(owned(PyObject_GetAttr(value, names[which])));
    const long n = PyLong_AsLong(got.ptr());
    if (n == -1 && PyErr_Occurred())
        throw nb::python_error();
    return static_cast<int>(n);
}
#endif

clock_fields date_fields(PyObject *v) {
#ifndef Py_LIMITED_API
    return {PyDateTime_GET_YEAR(v), PyDateTime_GET_MONTH(v), PyDateTime_GET_DAY(v), 0, 0, 0, 0, 0};
#else
    return {int_attr(v, year_i), int_attr(v, month_i), int_attr(v, day_i), 0, 0, 0, 0, 0};
#endif
}

int fold_of(PyObject *v) {
    const nb::object got = nb::steal(owned(PyObject_GetAttr(v, fold_name)));
    return PyObject_IsTrue(got.ptr());
}

clock_fields datetime_fields(PyObject *v) {
#ifndef Py_LIMITED_API
    return {PyDateTime_GET_YEAR(v),
            PyDateTime_GET_MONTH(v),
            PyDateTime_GET_DAY(v),
            PyDateTime_DATE_GET_HOUR(v),
            PyDateTime_DATE_GET_MINUTE(v),
            PyDateTime_DATE_GET_SECOND(v),
            PyDateTime_DATE_GET_MICROSECOND(v),
            PyDateTime_DATE_GET_FOLD(v)};
#else
    return {int_attr(v, year_i),   int_attr(v, month_i),  int_attr(v, day_i),         int_attr(v, hour_i),
            int_attr(v, minute_i), int_attr(v, second_i), int_attr(v, microsecond_i), fold_of(v)};
#endif
}

clock_fields time_fields(PyObject *v) {
#ifndef Py_LIMITED_API
    return {0,
            0,
            0,
            PyDateTime_TIME_GET_HOUR(v),
            PyDateTime_TIME_GET_MINUTE(v),
            PyDateTime_TIME_GET_SECOND(v),
            PyDateTime_TIME_GET_MICROSECOND(v),
            PyDateTime_TIME_GET_FOLD(v)};
#else
    return {0,
            0,
            0,
            int_attr(v, hour_i),
            int_attr(v, minute_i),
            int_attr(v, second_i),
            int_attr(v, microsecond_i),
            fold_of(v)};
#endif
}

timedelta_value delta_fields(PyObject *v) {
#ifndef Py_LIMITED_API
    return {PyDateTime_DELTA_GET_DAYS(v), PyDateTime_DELTA_GET_SECONDS(v), PyDateTime_DELTA_GET_MICROSECONDS(v)};
#else
    return {int_attr(v, days_i), int_attr(v, seconds_i), int_attr(v, microseconds_i)};
#endif
}

// The value's UTC offset in whole minutes; nullopt when it gives none.
std::optional<std::int16_t> offset_of(PyObject *value, const std::string &name) {
    const nb::object offset = nb::steal(owned(PyObject_CallMethodObjArgs(value, utcoffset_name, nullptr)));
    if (offset.is_none())
        return std::nullopt;
    // A subclass's utcoffset can return anything, and the full API's field macros trust the type.
#ifndef Py_LIMITED_API
    const bool delta = PyDelta_Check(offset.ptr());
#else
    const bool delta = instance(offset.ptr(), timedelta_type);
#endif
    if (!delta)
        raise(PyExc_TypeError, name + " needs utcoffset() to return a timedelta or None");
    const timedelta_value d = delta_fields(offset.ptr());
    const std::int64_t seconds = std::int64_t{d.days} * 86400 + d.seconds;
    if (seconds <= -86400 || seconds >= 86400)
        raise(PyExc_ValueError, name + " keeps a UTC offset of less than a day; the value's is not");
    if (d.microseconds != 0 || seconds % 60 != 0)
        raise(PyExc_ValueError, name + " keeps a UTC offset in whole minutes; the value's is not");
    return static_cast<std::int16_t>(seconds / 60);
}

bool has_tzinfo(PyObject *value) {
    const nb::object tz = nb::steal(owned(PyObject_GetAttr(value, tzinfo_name)));
    return !tz.is_none();
}

PyObject *zone(std::int16_t minutes) {
    std::atomic<PyObject *> &slot = zones[static_cast<std::size_t>(minutes + max_offset_minutes)];
    if (PyObject *z = slot.load(std::memory_order_acquire); z != nullptr)
        return z;
#ifndef Py_LIMITED_API
    const nb::object delta = nb::steal(owned(PyDelta_FromDSU(0, minutes * 60, 0)));
    PyObject *z = owned(PyTimeZone_FromOffset(delta.ptr()));
#else
    const nb::object delta = nb::steal(owned(PyObject_CallFunction(timedelta_type, "ii", 0, minutes * 60)));
    PyObject *z = owned(PyObject_CallFunctionObjArgs(timezone_type, delta.ptr(), nullptr));
#endif
    PyObject *expected = nullptr;
    if (!slot.compare_exchange_strong(expected, z, std::memory_order_acq_rel, std::memory_order_acquire)) {
        Py_DECREF(z);
        return expected;
    }
    return z;
}

std::int64_t micros_of_day(const clock_fields &f) {
    return ((std::int64_t{f.hour} * 60 + f.minute) * 60 + f.second) * 1000000 + f.microsecond;
}

[[noreturn]] void corrupt(const std::string &name, const char *what) {
    raise(PyExc_ValueError, name + ": the stored " + what + " is not a value Python can hold");
}

} // namespace

void init() {
    const nb::module_ datetime = nb::module_::import_("datetime");
    date_type = nb::object(datetime.attr("date")).release().ptr();
    time_type = nb::object(datetime.attr("time")).release().ptr();
    datetime_type = nb::object(datetime.attr("datetime")).release().ptr();
    timedelta_type = nb::object(datetime.attr("timedelta")).release().ptr();
    timezone_type = nb::object(datetime.attr("timezone")).release().ptr();
    uuid_type = nb::object(nb::module_::import_("uuid").attr("UUID")).release().ptr();
    decimal_type = nb::object(nb::module_::import_("decimal").attr("Decimal")).release().ptr();
    const nb::module_ abc = nb::module_::import_("collections.abc");
    mapping_abc = nb::object(abc.attr("Mapping")).release().ptr();
    sequence_abc = nb::object(abc.attr("Sequence")).release().ptr();
    set_abc = nb::object(abc.attr("Set")).release().ptr();
    const auto checked = [](PyObject *made) {
        if (made == nullptr)
            throw nb::python_error();
        return made;
    };
    utcoffset_name = checked(PyUnicode_InternFromString("utcoffset"));
    bytes_name = checked(PyUnicode_InternFromString("bytes"));
    fold_name = checked(PyUnicode_InternFromString("fold"));
    tzinfo_name = checked(PyUnicode_InternFromString("tzinfo"));
    empty_tuple = checked(PyTuple_New(0));
#ifdef Py_LIMITED_API
    const char *attrs[10] = {"year",   "month",       "day",  "hour",    "minute",
                             "second", "microsecond", "days", "seconds", "microseconds"};
    for (int i = 0; i < 10; ++i)
        names[i] = checked(PyUnicode_InternFromString(attrs[i]));
#else
    PyDateTime_IMPORT;
    if (PyDateTimeAPI == nullptr)
        throw nb::python_error();
#endif
}

PyObject *mapping() { return mapping_abc; }
PyObject *sequence() { return sequence_abc; }
PyObject *set() { return set_abc; }

bool accepts(std::uint8_t kind, PyObject *value) {
    switch (kind) {
    case kind_complex:
        return PyComplex_Check(value) || PyFloat_Check(value) || (PyLong_Check(value) && !PyBool_Check(value));
    case kind_date:
        return instance(value, date_type) && !instance(value, datetime_type);
    case kind_time:
        return instance(value, time_type);
    case kind_datetime:
        return instance(value, datetime_type);
    case kind_timedelta:
        return instance(value, timedelta_type);
    case kind_uuid:
        return instance(value, uuid_type);
    case kind_decimal:
        return instance(value, decimal_type);
    default:
        return false;
    }
}

void encode(std::uint8_t kind, PyObject *value, std::span<std::byte> out, const std::string &name) {
    if (!accepts(kind, value))
        wrong_type(kind, value, name);
    status rc = status::ok;
    switch (kind) {
    case kind_complex: {
        const double re = PyComplex_RealAsDouble(value);
        // ImagAsDouble may run Python code, which must not start with an exception set.
        const double im = PyErr_Occurred() ? 0.0 : PyComplex_ImagAsDouble(value);
        if (PyErr_Occurred()) {
            if (!PyErr_ExceptionMatches(PyExc_OverflowError))
                throw nb::python_error();
            PyErr_Clear();
            raise(PyExc_OverflowError, name + " holds two 64-bit floats; the value does not fit");
        }
        rc = encode_complex({re, im}, out);
        break;
    }
    case kind_date: {
        const clock_fields f = date_fields(value);
        rc = encode_date(std::chrono::sys_days(std::chrono::days(days_from_civil(f.year, f.month, f.day))), out);
        break;
    }
    case kind_time: {
        const clock_fields f = time_fields(value);
        const bool aware = has_tzinfo(value);
        const std::optional<std::int16_t> offset = aware ? offset_of(value, name) : std::nullopt;
        if (aware && !offset)
            raise(PyExc_ValueError, name + " needs a tzinfo that gives an offset without a date, such as a "
                                           "fixed datetime.timezone");
        rc = encode_time({std::chrono::microseconds(micros_of_day(f)), offset.value_or(0), !offset, f.fold != 0},
                         out);
        break;
    }
    case kind_datetime: {
        const clock_fields f = datetime_fields(value);
        const std::int64_t wall = days_from_civil(f.year, f.month, f.day) * micros_per_day + micros_of_day(f);
        const std::optional<std::int16_t> offset = offset_of(value, name);
        const std::int64_t micros = offset ? wall - std::int64_t{*offset} * 60000000 : wall;
        rc = encode_datetime({micros, offset.value_or(0), !offset, f.fold != 0}, out);
        break;
    }
    case kind_timedelta:
        rc = encode_timedelta(delta_fields(value), out);
        break;
    case kind_uuid: {
        const nb::object raw = nb::steal(owned(PyObject_GetAttr(value, bytes_name)));
        char *data = nullptr;
        Py_ssize_t size = 0;
        if (!PyBytes_Check(raw.ptr()) || PyBytes_AsStringAndSize(raw.ptr(), &data, &size) != 0 || size != 16)
            raise(PyExc_TypeError, name + " expects a UUID whose bytes are 16 bytes");
        std::memcpy(out.data(), data, 16);
        break;
    }
    default:
        raise(PyExc_SystemError, "scalars::encode got kind " + std::to_string(kind));
    }
    if (rc != status::ok)
        raise(PyExc_ValueError, name + ": the value is out of the range the layout keeps");
}

std::string decimal_text(PyObject *value, std::uint32_t capacity, const std::string &name) {
    if (!accepts(kind_decimal, value))
        wrong_type(kind_decimal, value, name);
    const nb::object text = nb::steal(owned(PyObject_Str(value)));
    Py_ssize_t size = 0;
    const char *data = PyUnicode_AsUTF8AndSize(text.ptr(), &size);
    if (data == nullptr)
        throw nb::python_error();
    if (static_cast<std::size_t>(size) > capacity)
        raise(PyExc_ValueError, name + " holds at most " + std::to_string(capacity) +
                                    " characters; the value's text takes " + std::to_string(size));
    return {data, static_cast<std::size_t>(size)};
}

PyObject *decode(std::uint8_t kind, std::span<const std::byte> bytes, const std::string &name) {
    try {
        switch (kind) {
        case kind_complex: {
            const auto z = decode_complex(bytes);
            return PyComplex_FromDoubles(z->real(), z->imag());
        }
        case kind_date: {
            const auto day = decode_date(bytes);
            if (!day)
                corrupt(name, "date");
            const civil c = civil_from_days(day->time_since_epoch().count());
#ifndef Py_LIMITED_API
            return PyDate_FromDate(c.year, c.month, c.day);
#else
            return PyObject_CallFunction(date_type, "iii", c.year, c.month, c.day);
#endif
        }
        case kind_time: {
            const auto t = decode_time(bytes);
            if (!t)
                corrupt(name, "time");
            std::int64_t us = t->of_day.count();
            const int micro = static_cast<int>(us % 1000000);
            us /= 1000000;
            PyObject *tz = t->naive ? Py_None : zone(t->offset);
#ifndef Py_LIMITED_API
            return PyDateTimeAPI->Time_FromTimeAndFold(static_cast<int>(us / 3600), static_cast<int>(us / 60 % 60),
                                                       static_cast<int>(us % 60), micro, tz, t->fold ? 1 : 0,
                                                       PyDateTimeAPI->TimeType);
#else
            const nb::object args = nb::make_tuple(us / 3600, us / 60 % 60, us % 60, micro, nb::borrow(tz));
            nb::dict kwargs;
            kwargs["fold"] = t->fold ? 1 : 0;
            return PyObject_Call(time_type, args.ptr(), kwargs.ptr());
#endif
        }
        case kind_datetime: {
            const auto v = decode_datetime(bytes);
            if (!v)
                corrupt(name, "datetime");
            const std::int64_t wall = v->local().time_since_epoch().count();
            std::int64_t days = wall / micros_per_day;
            std::int64_t rest = wall % micros_per_day;
            if (rest < 0) {
                rest += micros_per_day;
                --days;
            }
            const civil c = civil_from_days(days);
            const int micro = static_cast<int>(rest % 1000000);
            rest /= 1000000;
            PyObject *tz = v->naive ? Py_None : zone(v->offset);
#ifndef Py_LIMITED_API
            return PyDateTimeAPI->DateTime_FromDateAndTimeAndFold(
                c.year, c.month, c.day, static_cast<int>(rest / 3600), static_cast<int>(rest / 60 % 60),
                static_cast<int>(rest % 60), micro, tz, v->fold ? 1 : 0, PyDateTimeAPI->DateTimeType);
#else
            const nb::object args = nb::make_tuple(c.year, c.month, c.day, rest / 3600, rest / 60 % 60, rest % 60,
                                                   micro, nb::borrow(tz));
            nb::dict kwargs;
            kwargs["fold"] = v->fold ? 1 : 0;
            return PyObject_Call(datetime_type, args.ptr(), kwargs.ptr());
#endif
        }
        case kind_timedelta: {
            const auto d = decode_timedelta(bytes);
            if (!d)
                corrupt(name, "timedelta");
#ifndef Py_LIMITED_API
            return PyDelta_FromDSU(d->days, d->seconds, d->microseconds);
#else
            return PyObject_CallFunction(timedelta_type, "iii", d->days, d->seconds, d->microseconds);
#endif
        }
        case kind_uuid: {
            const nb::object raw = nb::bytes(reinterpret_cast<const char *>(bytes.data()), 16);
            nb::dict kwargs;
            kwargs["bytes"] = raw;
            return PyObject_Call(uuid_type, empty_tuple, kwargs.ptr());
        }
        case kind_decimal: {
            const nb::object text = nb::steal(PyUnicode_DecodeUTF8(
                reinterpret_cast<const char *>(bytes.data()), static_cast<Py_ssize_t>(bytes.size()), "strict"));
            if (!text.is_valid()) {
                if (!PyErr_ExceptionMatches(PyExc_UnicodeDecodeError))
                    throw nb::python_error();
                PyErr_Clear();
                corrupt(name, "Decimal");
            }
            PyObject *value = PyObject_CallFunctionObjArgs(decimal_type, text.ptr(), nullptr);
            // decimal raises InvalidOperation, an ArithmeticError, for text that is not a number.
            if (value == nullptr && PyErr_ExceptionMatches(PyExc_ArithmeticError)) {
                PyErr_Clear();
                corrupt(name, "Decimal");
            }
            return value;
        }
        default:
            raise(PyExc_SystemError, "scalars::decode got kind " + std::to_string(kind));
        }
    } catch (nb::python_error &e) {
        e.restore();
        return nullptr;
    }
}

} // namespace sharedbox::scalars
