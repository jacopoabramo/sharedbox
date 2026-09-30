---
icon: lucide/check-check
---

# How to run the commit checks

sharedbox checks formatting and lint with [`prek`](https://prek.j178.dev),
which runs the hooks listed in `prek.toml` at the project root. The same
hooks run on every commit once installed, in `uv run tox -e lint`, and in
CI.

## Before you start

[Set up a development environment](set-up-development.md). `prek`, `ruff`
and `clang-format` are in the `lint` dependency group, which `uv sync`
installs.

## Install the git hook

Once per clone, from the project root:

```bash
uv run prek install
```

This writes `.git/hooks/pre-commit`. From then on, `git commit` runs the
hooks on the staged files and stops the commit if one fails.

## Run the hooks by hand

```bash
uv run prek run                         # staged files only
uv run prek run --all-files             # every tracked file, as CI does
uv run prek run ruff-check --all-files  # a single hook
uv run tox -e lint                      # every tracked file, in its own environment
```

## What the hooks check

| hook | what it does |
| --- | --- |
| `end-of-file-fixer` | ends every file with exactly one newline |
| `trailing-whitespace` | strips spaces at the end of lines |
| `check-yaml`, `check-toml` | fails on files that do not parse |
| `check-added-large-files` | fails on large files added to the index |
| `check-merge-conflict` | fails on leftover conflict markers |
| `ruff-check` | `ruff check --fix` on Python files and stubs |
| `ruff-format` | `ruff format` on Python files and stubs |
| `clang-format` | `clang-format -i` on the C and C++ files in `src/sharedbox/_native/`, `include/sharedbox/` and `tests/cpp/` |

`ruff` is set up in `pyproject.toml`. Besides its default rules it checks
docstrings in the numpydoc format (`D`) in `src/sharedbox`, and imports
anywhere but the top of a module (`E402`, `PLC0415`). `clang-format` follows
`.clang-format` at the project root.

## When a hook fails

A hook that can fix what it found rewrites the file and still reports a
failure, so the commit stops with the fix unstaged. Review the change, stage
it with `git add`, and commit again. A `ruff` violation that `--fix` cannot
repair is printed with its rule code and has to be fixed by hand.

CI runs the hooks on every tracked file. Any file a hook rewrites fails the
build there.

## Which versions run

The hooks run `uv run --locked --only-group lint <tool>`, so they use the
versions pinned in `uv.lock`, the same ones `tox` and CI use. Updating a
tool in the lock file updates the hook too; `prek.toml` holds no separate
version. `--only-group lint` makes a fresh clone install only the three
tools before the hooks run.

## Next steps

- [How to run the tests](run-tests.md)
