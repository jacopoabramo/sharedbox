---
icon: lucide/git-pull-request
---

# How to write commits and pull requests

Commits and pull requests are read long after they are written, often by
someone looking for when and why something changed. Keeping them short and
in one shape makes that search easy.

## Commit messages

A commit message is one line in the
[Conventional Commits](https://www.conventionalcommits.org) form, with no
body:

```
type(scope): summary
```

- `type` is one of `feat`, `fix`, `docs`, `refactor`, `perf`, `test`,
  `build`, `ci`, `style`, `chore`. Add `!` after it for a change that
  breaks existing code: `feat!: ...`.
- `scope` is optional and names the part changed: `events`, `refs`,
  `benchmarks`, `stress`.
- The summary is in the imperative ("add", "fix", "move"), with no full
  stop, and the whole line is 72 characters or fewer.

Write what changed in plain words, so someone who has not seen the diff
understands it:

```
fix(events): rebuild again after a fork rebuild fails to read
```

A change too large to describe in one line is split into several commits.

## Issues

An issue title follows the same form as a commit message, naming the change
it asks for: `fix: attach waits forever on a half-created segment`,
`feat: add float array fields`.

## Pull requests

Open the pull request against `main`. The title follows the same rules as a
commit message.

Split the description into the sections that apply, each a few short
bullets:

- `## Summary`: what the pull request does, in a line or two.
- `## Changes`: one short bullet per change.
- `## Breaking changes`: what existing code must change, if anything.
- `## Testing`: the checks you ran and their result.

## Changelog entries

A change that someone using sharedbox would notice adds an entry to
`CHANGELOG.md` in the same pull request. The file follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/): entries go under
the release at the top, marked `Unreleased`, in one of the sections
`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed` and `Security`.

Each entry names the public symbol first, then says what changed:

```markdown
- `SharedBox.watch()`: no longer yields the same value twice.
```

An entry records what changed and nothing else. The reasons belong in the
pull request.
