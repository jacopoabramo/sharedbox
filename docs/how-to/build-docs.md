---
icon: lucide/book
---

# How to build the docs

## Before you start

[Set up a development environment](set-up-development.md).

## Build with Zensical

From the project root:

```bash
uv run tox -e docs
```

This builds the site with [Zensical](https://zensical.org), then runs
`scripts/check_xrefs.py` over it. The environment installs what the docs
need by itself, from `uv.lock`. It does not build the extension: the API
pages are made from the sources without importing them.

Zensical lives in the `docs` dependency group, which `uv sync` does not
install, so a command running it directly names the group:

```bash
uv run --group docs zensical build     # build only, no check
```

The site lands in `site/`. Serve it locally with:

```bash
uv run --group docs zensical serve
```

The server listens on `http://localhost:8000` and rebuilds on every change.

## What the check reports

`zensical build` reports no issues in three cases that leave the
page wrong, so `check_xrefs.py` looks for them in the built site:

- `unresolved [...]`: a cross-reference such as
  ``[`attach`][sharedbox.SharedBox.attach]`` names no object. The page
  shows the brackets as text.
- `snippet not included`: a line that includes part of a script was not
  read, usually because the part's name or the script's path is misspelt.
  The page shows the line in place of the code.
- `no #... in ...`, `names a page that does not exist`, `leaves the site`:
  a link names a part of a page, or a page, that is not there.

The script prints each problem with the page it is on and exits with an
error, which fails the `tox` environment and CI. When it finds nothing it
prints `no unresolved cross-references, snippets or links`.

## Troubleshooting

### `zensical` is not found

`uv run zensical` without `--group docs` fails with
`Failed to spawn: zensical`. Add the flag, or install the group once:

```bash
uv sync --group docs
```

### Port already in use

Pick another port:

```bash
uv run --group docs zensical serve --dev-addr localhost:8080
```

## Next steps

- [How to write documentation](write-docs.md)
- [How to run the tests](run-tests.md)
