---
icon: lucide/heart-handshake
---

# How to contribute

Thanks for wanting to improve `sharedbox`. This page walks you through how
a change gets from your idea into a release. If you only want to use
`sharedbox` in your own code, start with the
[tutorial](../tutorials/share-a-record.md) instead.

## How a change goes in

1. Open an issue describing the bug or the feature, so the change is
   agreed on before you write it. A small fix can skip this.
2. Set up your environment once: see
   [How to set up a development environment](set-up-development.md).
3. Branch from `main`, named after the kind of change and what it does:
   `fix/attach-timeout`, `feat/float-arrays`, `docs/glossary`.
4. Make the change, with tests. Run the checks before you push:
   see [How to run the tests](run-tests.md) and
   [How to run the commit checks](run-commit-checks.md).
5. Open a pull request against `main`, following
   [How to write commits and pull requests](commits-and-prs.md).
6. Wait for CI and a review. CI builds the wheels, runs the tests
   against each of them, runs the C++ tests and checks the docs. A reviewer
   may ask for changes; push them to the same branch.

## The guides for each step

- [How to set up a development environment](set-up-development.md)
- [How to run the tests](run-tests.md)
- [How to run the commit checks](run-commit-checks.md)
- [How to write commits and pull requests](commits-and-prs.md)
- [How to make a release](make-a-release.md)
- [How to write documentation](write-docs.md)
- [How to build the docs](build-docs.md)

A change written with the help of an AI tool follows the
[AI contribution policy](ai-contribution-policy.md).
