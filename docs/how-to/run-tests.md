---
icon: lucide/flask-conical
---

# How to run the tests

Run the Python tests, the type checks, the C++ tests of the header and the
longer property and stress runs.

## Before you start

[Set up a development environment](set-up-development.md). Run `uv sync`
after changing any C++ file, so the tests use the new extension.

## Run the Python tests

```bash
uv run pytest
```

This runs `tests/` on the interpreter of `.venv`. Many tests start other
processes. Pick tests with the usual `pytest` arguments:

```bash
uv run pytest tests/test_box.py
uv run pytest tests/test_box.py -k pickle
```

Split the suite across your CPU cores with `pytest-xdist`:

```bash
uv run pytest -n auto --dist loadfile
```

`--dist loadfile` keeps the tests of one file in one worker.

### Give each test its own segment name

Two tests that create a [segment](../explanation/glossary.md#segment) under
the same name fail when they run at once. Take the name from the
`unique_name` fixture, which gives a new name to each test and, on Linux,
removes the segment from `/dev/shm` afterwards. The `names` fixture makes
several names from it and removes each of them too.

A test that needs a fixed name builds it from the `SHAREDBOX_TEST_RUN`
environment variable. `tests/conftest.py` sets it once per `pytest` run and
every process the run starts inherits it, so the name stays the same within
a run and differs between runs. This is what lets several workers, or
several `tox` environments, run at the same time.

On CI the Linux wheels are tested in a container with 1 GiB of shared
memory, so keep the segments a test creates well under that.

## Run every environment with tox

`tox` builds each environment from `uv.lock`, so your result matches CI's:

```bash
uv run tox          # every environment, one after the other
uv run tox -p auto  # the same, in parallel
uv run tox -e py314t
```

| environment | what it runs |
| --- | --- |
| `py311`, `py312`, `py313`, `py314` | `pytest -q` on that CPython version |
| `py314t` | `pytest -q` on free-threaded CPython 3.14 |
| `mypy` | `mypy` in strict mode, then `stubtest` against `src/sharedbox/_native.pyi` |
| `lint` | `prek run --all-files`: the [commit checks](run-commit-checks.md) |
| `docs` | the docs build and the cross-reference check: see [How to build the docs](build-docs.md) |

Arguments after `--` go to `pytest`:

```bash
uv run tox -e py312 -- tests/test_box.py -k pickle
```

`mypy` checks `src`, `tests`, `docs/tutorials` and `docs/examples`.
`src/sharedbox/_native.pyi` is written by hand; when the functions or
classes the extension exposes change, update it too, and `stubtest` reports
where the two disagree.

## Run the C++ tests

The tests of `sharedbox.hpp` and `sharedbox_c.h` are in `tests/cpp/`. They
use [doctest](https://github.com/doctest/doctest) and CTest, and add this
repository the way a project using CMake's `FetchContent` does:

```bash
cmake -S tests/cpp -B build-cpp -DCMAKE_BUILD_TYPE=Release
cmake --build build-cpp --config Release
ctest --test-dir build-cpp -C Release --output-on-failure
```

CI runs them on Linux and Windows, and again on Linux with
AddressSanitizer and UndefinedBehaviorSanitizer.

### The C consumer

`tests/test_capsule.py` builds the C library in `tests/cpp/consumer/`
against the installed wheel with CMake, then loads it and passes it a box.
The tests that need it are skipped when CMake is not on `PATH`. Set
`SHAREDBOX_REQUIRE_C_CONSUMER=1` to make it fail instead, as CI does:

```bash
SHAREDBOX_REQUIRE_C_CONSUMER=1 uv run pytest tests/test_capsule.py
```

## Run the property tests longer

The property tests, `tests/test_properties_*.py`, use
[Hypothesis](https://hypothesis.readthedocs.io). They run with the rest of
the suite under the `ci` profile, which tries 50 examples per test. The
`thorough` profile tries 2000:

```bash
uv run pytest tests/test_properties_codec.py --hypothesis-profile=thorough
```

## Run the stress tests

The stress tests in `tests/stress/` measure timing, throughput and memory.
They carry the `stress` marker and are left out of a normal run:

```bash
uv run pytest -m stress                              # about 3 minutes
SHAREDBOX_STRESS_SCALE=0.05 uv run pytest -m stress  # a short run
```

`SHAREDBOX_STRESS_SCALE` multiplies every duration and count; `1` is the
default. The tests write their numbers to `build/stress/*.json` and print
them at the end of the run.

On CI the stress tests run only when the workflow is started by hand, on
Linux and Windows against the CPython 3.12 wheel. That job also runs the
property tests with the `thorough` profile.

## Check the speed of a change

For a change to the code that reads or writes a field, compare the speed of
your branch with `main` on Windows and CPython 3.11. Run this three times on
your branch and three times on `main`, in the same session:

```bash
uv run benchbox ops --fast --filter "*int*"
```

Then compare the medians of `read int/SharedBox` and `write int/SharedBox`.
[How to run the benchmarks](run-benchmarks.md) describes `benchbox`.
