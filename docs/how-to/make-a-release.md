---
icon: lucide/package
---

# How to make a release

Releasing `sharedbox` means publishing a GitHub release; CI does the rest.
Publishing it starts CI, which builds the wheels, tests them and uploads
them to PyPI, so most of this guide is about getting the details right
before you press the button.

## Before you start

[`gh`](https://cli.github.com) signed in with write access to the
repository, and a clone of it.

## 1. Date the changelog

`CHANGELOG.md` collects the entries of the next release under a heading
marked `Unreleased`, as
[How to write commits and pull requests](commits-and-prs.md#changelog-entries)
describes. On an up-to-date `main`, make a release branch and replace
`Unreleased` with today's date, written `DD-MM-YYYY`:

```bash
git switch main
git pull
git switch -c release/v0.3.0
```

```markdown
## [0.3.0] - 14-10-2026
```

Read the section once more, then commit it, open a pull request and merge
it once the checks pass:

```bash
git commit -am "docs: date the 0.3.0 changelog"
git push origin release/v0.3.0
gh pr create --base main --title "docs: date the 0.3.0 changelog" \
  --body "Dates the changelog section for 0.3.0."
```

## 2. Publish the release

Create the release on the merge commit. `gh` creates the tag too:

```bash
git switch main
git pull
gh release create v0.3.0 --target main --title v0.3.0 \
  --notes "Changes: https://github.com/jacopoabramo/sharedbox/blob/v0.3.0/CHANGELOG.md"
```

The release notes link to the changelog as it is in the tag.

The tag is `vX.Y.Z`. The package version comes from it, through
setuptools-scm, so the tag must be on the commit you want to release.

## What CI does with it

Publishing the release runs CI on the tagged commit. It first builds the
wheels and tests each one. It then checks that the tag has the form
`vX.Y.Z` or `vX.Y.ZrcN`, that the pre-release flag matches (set for `rc`
tags, not set for the others), builds the source distribution and checks
that its version is the tag's. A mismatch fails the build before anything
is uploaded to PyPI.

Once the lint checks, the docs build and the C++ tests pass as well, CI
uploads the wheels and the source distribution to
[PyPI](https://pypi.org/project/sharedbox/). A final release also
publishes the documentation site. The site is published on every push to
`main` as well.

## Release candidates

Leave `Unreleased` in the changelog, tag the candidate `vX.Y.ZrcN` and
mark the release as a pre-release:

```bash
gh release create v0.3.0rc1 --target main --title v0.3.0rc1 --prerelease \
  --notes "Changes: https://github.com/jacopoabramo/sharedbox/blob/main/CHANGELOG.md#unreleased"
```

Its notes link to the `Unreleased` section of the changelog on `main`.

A candidate uploads the package to PyPI and leaves the documentation site
as it is.
