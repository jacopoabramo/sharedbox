---
icon: lucide/package
---

# How to make a release

Releasing `sharedbox` means writing its changelog section and then
publishing a GitHub release. CI then builds the wheels, tests them and
uploads them to PyPI, so most of this guide is about getting the section
right before you publish.

## Before you start

You need write access to the repository. Every step happens in GitHub's web
interface.

Don't merge other pull requests between the first step and the last,
because the release takes `main` as it is when you publish it.

## 1. Write the changelog section

The entries of the next release wait in `changelog.d/`, one file per change,
as [How to write commits and pull requests](commits-and-prs.md#changelog-entries)
describes. The `Prepare release` workflow collects them into a section of
`CHANGELOG.md`. Open Actions -> Prepare release -> Run workflow, and give it
the version without its `v`, such as `0.6.0`.

The run writes the section, dated today, deletes the entry files and opens
the pull request "docs: write the 0.6.0 changelog", whose description is the
section. To read the section first without changing anything, tick "Only
show the changelog section": the run then shows it in its summary and opens
no pull request.

## 2. Merge the changelog pull request

Read the section in `CHANGELOG.md` on the pull request's branch, and fix any
entry that reads badly there. Checks of a pull request opened by a workflow
wait for approval, so click "Approve workflows to run", then merge once they
pass.

## 3. Publish the release

Open Releases -> Draft a new release. Under "Choose a tag", type `v0.6.0`,
pick "Create new tag on publish" and target `main`. Title the release
`v0.6.0`, leave the description empty, and publish it.

The tag is `vX.Y.Z`. The package version comes from it, through
setuptools-scm, so the tag must be on the commit you want to release.

## What CI does with it

Publishing the release runs CI on the tagged commit. It first builds the
wheels and tests each one. It then checks that the tag has the form
`vX.Y.Z` or `vX.Y.ZrcN`, that the pre-release flag matches (set for `rc`
tags, not set for the others), and that `CHANGELOG.md` has a section for a
final release. Then it builds the source distribution and checks that its
version is the tag's. A failed check stops the build before anything is
uploaded to PyPI.

Once the lint checks, the docs build and the C++ tests pass as well, CI
uploads the wheels and the source distribution to
[PyPI](https://pypi.org/project/sharedbox/). For a final release it then
writes the changelog section into the release's description, and publishes
the documentation site. The site is published on every push to `main` as
well.

A release published before its changelog pull request merged fails that
check, so nothing reaches PyPI. Delete the release and its tag, merge the
pull request, and publish again.

## Release candidates

A release candidate skips the first two steps. Publish the release as in
step 3 with a tag such as `v0.6.0rc1`, and tick "Set as a pre-release".
CI uploads the package to PyPI, writes no notes and leaves the documentation
site as it is. The entries stay in `changelog.d/` for the final release.
