---
icon: lucide/bot
---

# AI contribution policy

!!! note

    This policy is adapted from the zarr-python AI contribution policy:
    [AI-assisted contributions](https://zarr.readthedocs.io/en/main/contributing/#ai-assisted-contributions).

Many people now write code with AI tools. You may use them for sharedbox,
but every contribution is held to the same standard, whether you wrote it
by hand, with AI assistance, or had an AI tool generate all of it.

## You are responsible for your changes

If you submit a pull request, you are responsible for understanding and
having fully reviewed the changes. You must be able to explain why each
change is correct and how it fits into the project.

## Communication must be your own

Pull request descriptions, issue comments and review responses must be in
your own words. The substance and reasoning must come from you. Using AI to
fix grammar or phrasing is fine, but do not paste AI-generated text as
comments or review responses.

## Review every line

You must have read and understood every change before you submit it. If you
used AI to generate code, read it critically and test it. The pull request
description explains the approach and the reasoning; do not leave it to
reviewers to work out what the code does and why.

## Keep pull requests reviewable

Generating code with AI is fast; reviewing it is not. A large diff moves the
work from the contributor to the reviewer. A pull request that cannot be
reviewed in reasonable time with reasonable effort may be closed, however
useful or correct it may be. Use AI tools to prepare pull requests that are
easier to review, too: well-structured commits, clear descriptions and a
small scope.

If you plan a large AI-assisted contribution, such as a significant refactor
or a new field type, open an issue first to discuss the scope and the
approach. Maintainers may also ask for a large change to be split into
smaller pieces.

## Documentation

The same rules apply to documentation. sharedbox has rules of its own that
AI tools often get wrong: the byte layout of a
[segment](../explanation/glossary.md#segment), what a
[sequence lock](../explanation/glossary.md#sequence-lock) guarantees to a
reader, when a [box](../explanation/glossary.md#box)'s name is removed,
and what differs between Linux and Windows. Do not submit documentation
you have not read carefully and checked against the code.
