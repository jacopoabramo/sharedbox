# CLAUDE.md

`sharedbox` gives Python processes a record that lives in shared memory.
`SharedBox` is a base class: a subclass's annotated fields are stored in one
named segment that every process can open. The segment is a plain named
mapping laid out by `include/sharedbox/sharedbox.hpp` (C++20, header-only),
which the nanobind extension runs on; `sharedbox_c.h` is a minimal C
interface to the same header. Boost is not used. The Python side decides
the layout and the native module converts values.

## Repository layout

```text
sharedbox/
|-- include/sharedbox/
|   |-- sharedbox.hpp          layout 2.0 (and 1.0 reading) and its protocols: create, publish, open, lock, read, write, description table, waiter slots, capsule handle
|   |-- sharedbox_c.h          minimal C interface: sbx_open, sbx_import, sbx_read, sbx_write, sbx_schema_hash, sbx_release, sbx_field_desc, typed sbx_read_* and sbx_write_*
|   `-- sharedbox_c.cpp        its implementation, compiled by the consumer (CMake target sharedbox::c)
|-- cmake/
|   |-- sharedbox-config.cmake       find_package(sharedbox) from an installed wheel; the build writes
|   |                                sharedbox-config-version.cmake next to it in the wheel
|   `-- sharedbox-require-cxx.cmake  stops configure when sharedbox::c is linked without CXX
|-- src/sharedbox/
|   |-- __init__.py            re-exports the public API, get_include()
|   |-- _box.py                SharedBox: class keywords, fields, fields(), create/attach, __post_init__, update, snapshot, unlink, __sharedbox_box__
|   |-- _layout.py             Capacity, field() and Field, field offsets and kind codes, schema hash
|   |-- _types.py              annotation parsing into TypeSpec, the description table
|   |-- _arrays.py             Shape, DType, SupportsDLPack, register_array_type
|   |-- _events.py             BoxEvents, FieldWatch, the watcher thread
|   |-- _follow.py             BoxEvents.follow and unfollow: forwarding the events of boxes that references reach
|   |-- _refs.py               reference fields: BoxRef, the class registry, BrokenReferenceError, UnknownBoxClassError
|   |-- _native.pyi            hand-written stub for the extension
|   |-- py.typed
|   |-- benchmarks/            the benchbox command (extra: benchmarks)
|   |   |-- cli.py             entry point; exits with an install hint without Typer
|   |   |-- _app.py            Typer commands: ops, roundtrip, contention, size, plot, all
|   |   |-- ops.py             pyperf timings of single operations, against the stdlib
|   |   |-- roundtrip.py       cross-process round trip percentiles
|   |   |-- contention.py      throughput and percentiles with several writer and reader processes
|   |   |-- plot.py            SVG charts of the ops and roundtrip results (matplotlib)
|   |   |-- size.py            wheel and extension size (standard library only)
|   |   `-- size_diff.py       wheel size table against main, for CI (standard library only)
|   `-- _native/
|       |-- module.cpp         nanobind module: Segment, the Field descriptor, the BoxMethod type
|       |                      (native update and snapshot) and the error classes
|       |-- codec.{hpp,cpp}    converts field values to and from their stored bytes; encode_all
|       |                      checks every value of an update before any is written,
|       |                      with_record reads the whole record at once
|       |-- types.{hpp,cpp}    Types: conversions of kinds above 5, arrays
|       |-- scalars.{hpp,cpp}  kinds 6 to 12, the datetime C API on full-API builds
|       `-- segment.{hpp,cpp}  Segment: a sharedbox::handle plus error messages and the lifetime lock
|-- tests/                     pytest; many tests spawn processes
|   |-- crossproc.py           helpers that run a box in another process
|   |-- forged_types.py        builds segments with forged description tables
|   |-- test_benchbox_contention.py  benchbox contention, a short run
|   |-- test_benchbox_plot.py  benchbox plot; skipped without matplotlib and pyperf
|   |-- test_capsule.py        __sharedbox_box__, and the C consumer in tests/cpp/consumer/
|   |-- test_doc_examples.py   runs each docs/examples/*.py script
|   |-- test_doc_tutorials.py  runs docs/tutorials/motor.py and checks the output its pages show
|   |-- test_follow.py         BoxEvents.follow, unfollow and nested
|   |-- test_native_waiters.py waiter slots, dead owners, interrupt
|   |-- test_properties_*.py   Hypothesis property tests
|   |-- stress/                stress tests, marker stress, deselected by default
|   |-- cpp/                   C++ tests (doctest, CTest) and the C smoke test
|   |   `-- consumer/          a C library built against an installed wheel, loaded by test_capsule.py
|   `-- type_checks/           checked by mypy, never imported at run time
|-- benchmarks/                not collected by the default pytest run
|   `-- test_bench_box.py      pytest-codspeed benchmarks, run by codspeed.yml
|-- scripts/
|   |-- vscode_setup.py        points VS Code's C/C++ extension at the build headers
|   `-- check_xrefs.py         reports unresolved cross-references, unread snippets and broken links in site/
|-- docs/                      Diataxis site built by Zensical: tutorials/, how-to/, explanation/, reference/
|   |-- tutorials/motor.py     the script the three tutorials build and include
|   |-- examples/              one script per how-to guide, included by the guide
|   `-- reference/segment-layout.md  layout 2.0 and 1.0, names and protocols
|-- includes/abbreviations.md  acronym tooltips appended to every page
|-- zensical.toml              site configuration and navigation
|-- .github/workflows/ci.yaml  lint, docs check, C++ tests, cibuildwheel wheels, tests, stress,
|                              PyPI publish, site publish
|-- .github/workflows/check-docs.yaml    builds the site and runs check_xrefs.py
|-- .github/workflows/publish-docs.yaml  deploys the checked site to GitHub Pages
|-- .github/workflows/codspeed.yml  benchmarks on CodSpeed
|-- .github/workflows/contention.yaml  benchbox contention on Linux and Windows, for pull requests
|                              that touch the native code, and by hand
|-- CMakeLists.txt             sharedbox::headers, sharedbox::c, extension build
|-- stubtest-allowlist.txt     stubtest exceptions for nanobind types
|-- .clang-format              clang-format style for the C and C++ sources
|-- prek.toml                  prek hooks: ruff, clang-format, and the builtin checks
|-- pyproject.toml             scikit-build-core, setuptools-scm, pytest, cibuildwheel, mypy, tox
`-- uv.lock
```

- `_native.pyi` is written by hand. Update it whenever the bound API changes;
  the tox `mypy` env runs stubtest against it.

## Memory layout

The specification is `docs/reference/segment-layout.md`; the reasons are in
the pages of `docs/explanation/`.

One mapping per box, named `sharedbox.<name>`: `/dev/shm/sharedbox.<name>`
on Linux (mode `0600`), `Local\sharedbox.<name>` on Windows (page-file
backed). Windows waiter slot `i` also has an auto-reset event
`Local\sharedbox.<name>.w<i>`. The box name is the `name` class keyword,
the name passed to `create()`, or by default 16 hex digits of SHA-256 over
the class's identity (the `identity` class keyword, by default
`module.qualname`, with `__mp_main__` counted as `__main__`). Creating
always asks for a new name and raises `SegmentExistsError` if it is taken.

Layout 2.0, from offset 0:

- Header, 128 bytes. Line 0, written once at creation: `magic`,
  `layout_major` 2, `layout_minor` 0, `field_count`, `waiter_slots`,
  `schema_hash`, `record_size`, `record`, `tail` (always 128), `size`,
  `types_size` (offset 60; 0 in layout 1.0), and the creator fields
  `create_id`, `creator_start`, `creator_pid`. Line 1, changed by writes
  and waits: `seq` (sequence lock; the generation is
  `seq >> 1`, there is no generation field), `writer_pid`, `wake_word`,
  `waiters`, `creator_pidns`. Both lines keep reserved zero bytes.
- The tail: `field_count` field table entries of 8 bytes (`u32 offset`,
  `u32 capacity_and_kind`: top 8 bits the kind code, the low 24 the
  capacity or size, or for kinds 64 and up the offset of the field's
  description), one `u64` write count per field, then `waiter_slots` slots
  of 24 bytes (`owner_start`, `owner_pidns`, `owner_pid`, `interrupt`),
  then the description table of `types_size` bytes. Kinds are 0 to 5 as
  in layout 1.0, 6 to 12 (fixed-size scalars) and 64 to 74 (described
  types).
- The record, at a multiple of 64. The mapping size is rounded up to 4 KiB.
  `record + record_size <= 2**32 - 4096`.

`magic` is stored last on create, with release ordering, after the initial
values are in the record; attach waits for it. Attach accepts
`layout_major` 1 and 2 and refuses another, checks every geometry field
against the mapping size, copies the field table and uses only the copy.
`static_assert`s in `sharedbox.hpp` check every `sizeof` and `offsetof`.

### Record encoding

Fields are packed by descending alignment, not declaration order, each
starting at a multiple of its own alignment. The native module converts
values to and from these bytes; nothing is pickled. Everything is
little-endian. `docs/reference/segment-layout.md` gives every kind and its
bytes.

At most 256 fields; a capacity is 1 byte to 1 MiB, and a collection's
capacity counts elements, 1 to 1048576.

### Lifecycle

- `close()` interrupts and stops the watcher thread, stops forwarding
  started with `events.follow`, and detaches this box. Later reads and
  writes raise `BoxClosedError`. Garbage collection closes a box too, but
  forwarding it started keeps running until the cycle collector frees its
  events groups.
- `__sharedbox_box__()` returns a capsule whose handle has its own mapping
  of the segment; `close()` and `unlink()` do not affect it.
- `unlink()` removes the name on Linux and does nothing on Windows, where the
  OS frees the segment with its last handle.
- A Linux segment that is never unlinked stays in `/dev/shm`. Most tests use the
  `unique_name` fixture, which removes `/dev/shm/sharedbox.<name>` afterwards.
- On Linux every open box keeps a file descriptor, so about 1000 open boxes
  reach the default `ulimit -n` of 1024.

## Build

scikit-build-core drives CMake (3.30 or newer), which needs a C++20
compiler (MSVC or GCC) and nanobind (a build dependency); it needs
neither `VCPKG_ROOT` nor Boost. macOS is not supported.

The version comes from git tags through setuptools-scm, written to
`src/sharedbox/_version.py` (ignored by git).

```sh
uv sync --dev                          # creates .venv, builds and installs the extension
uv run python scripts/vscode_setup.py  # once, for VS Code's C/C++ extension
```

`[tool.uv] cache-keys` lists the C++ sources, headers and `cmake/`, so
`uv sync` rebuilds the extension after they change. Build folders are
`build/<wheel tag>`. The wheel also carries `include/sharedbox/` under
`sharedbox/include/` and the CMake config and config version file under
`sharedbox/share/cmake/sharedbox/`.

Wheels per platform (Windows x64, Linux x86_64 glibc and musl): `cp311-cp311`,
`cp312-abi3` for CPython 3.12 and newer, and `cp314-cp314t` for free-threaded
CPython 3.14. One `nanobind_add_module(... STABLE_ABI FREE_THREADED ...)` line
produces all three.

## Tests

```sh
uv run pytest                          # current interpreter
uv run pytest tests/test_box.py -k pickle
uv run pytest -n auto --dist loadfile  # same suite, split across files
uv run tox                             # py311 to py314, py314t, mypy, lint, docs
uv run tox -e py314t                   # one env
uv run tox -p auto                     # same environments, in parallel
```

`tests/conftest.py` sets `SHAREDBOX_TEST_RUN` once per pytest run and every
process it spawns inherits it, so the fixed segment names in
`tests/test_box.py` (`Motor`, and the two classes in
`test_default_name_ignores_the_spawned_main_module`) stay unique to that
run. That is what lets `-n auto --dist loadfile` and `tox -p auto` run
several workers or environments at once without fighting over the same
segment name.

C++ tests, in `tests/cpp/` (they add this repository the way a
`FetchContent` user does):

```sh
cmake -S tests/cpp -B build-cpp -DCMAKE_BUILD_TYPE=Release
cmake --build build-cpp --config Release
ctest --test-dir build-cpp -C Release --output-on-failure
```

`tests/test_capsule.py` builds the C consumer in `tests/cpp/consumer/`
against the installed wheel with CMake and skips when CMake is missing;
`SHAREDBOX_REQUIRE_C_CONSUMER=1` makes that a failure. The `cpp_header` CI
job runs the C++ tests on Linux and Windows, again on Linux with
AddressSanitizer and UndefinedBehaviorSanitizer, then the C consumer with
that variable set.

Performance check for changes to the read or write path, on Windows
cp311: run `uv run benchbox ops --fast --filter "*int*"` three times on
the branch and three times on `main` in the same session, and compare the
medians of `read int/SharedBox` and `write int/SharedBox`.

Property tests (`tests/test_properties_*.py`) run in the normal suite under
the Hypothesis profile `ci` (50 examples); `--hypothesis-profile=thorough`
runs 2000.

Stress tests (`tests/stress/`) carry the `stress` marker and are deselected
by default:

```sh
uv run pytest -m stress                              # about 3 minutes
SHAREDBOX_STRESS_SCALE=0.05 uv run pytest -m stress  # a short run
```

They write their numbers to `build/stress/*.json` and print them at the end
of the run. On CI the `stress` job runs only when the workflow is started
by hand (`workflow_dispatch`), on ubuntu-latest and windows-latest against
the cp312 wheel, and also runs the property tests with the `thorough`
profile.

CI (`.github/workflows/ci.yaml`) builds the wheels above with cibuildwheel,
runs pytest against each wheel, and publishes to PyPI. Its `docs` job runs
`check-docs.yaml` (build the site, then `check_xrefs.py`), and
`publish_docs` publishes the site from `main` and from final releases.
Publishing to PyPI runs only from a GitHub release tagged `vX.Y.Z` and
marked as a release, or `vX.Y.ZrcN` and marked as a pre-release; any other
tag or mismatch between the tag and the pre-release flag fails the build
before it uploads. Docker runs with `--shm-size=1g`, so keep test
segments under that.

## Usage

```python
import multiprocessing as mp
from typing import Annotated

from sharedbox import Capacity, SharedBox


class Motor(SharedBox):
    position: int
    enabled: bool
    label: Annotated[str, Capacity(32)]


def worker() -> None:
    motor = Motor.attach()          # finds the box by its class
    motor.position = 10
    motor.close()


if __name__ == "__main__":
    with Motor(1, False, "x-axis") as motor:
        motor.events.position.connect(lambda new, old: print(old, "->", new))
        child = mp.Process(target=worker)
        child.start()
        child.join()                      # prints: 1 -> 10
    Motor.unlink()
```

A read decodes the stored bytes; a value that cannot be changed (a
`datetime`, or a frozen record, tuple or frozenset holding only such values)
is reused until its field is written. `update(**values)` writes several
fields at once; `watch(field)` and `events` report changes from any process. C++ and C code take a box through `__sharedbox_box__` or open it by
name; see `docs/how-to/accept-a-box-in-cpp.md`,
`docs/how-to/accept-a-box-in-c.md` and
`docs/how-to/open-a-box-from-a-program.md`.

## Docs

Build and check the site with `uv run tox -e docs`. The rules for pages,
docstrings, and tutorial and example scripts are in
`docs/how-to/write-docs.md`; the contributing guides are the other pages of
the Contributing section in `zensical.toml`.

## Conventions

- Changelog: `CHANGELOG.md`, Keep a Changelog format, dates as `DD-MM-YYYY`.
- Lint and format Python with `ruff` and C and C++ with `clang-format`
  (`src/sharedbox/_native/`, `include/sharedbox/`, `tests/cpp/`), both run
  through prek: `uv run prek run --all-files`, `uv run tox -e lint`.
- Change dependencies with `uv add` / `uv remove`, never by editing
  `pyproject.toml`.
- Docstrings in `.py` and `.pyi` use the numpydoc format; cross-references
  are mkdocs-style Markdown (``[`name`][path]``), never reStructuredText roles.
