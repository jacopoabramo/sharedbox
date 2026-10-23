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

Each change someone using sharedbox would notice gets a file in
`changelog.d/`, named after the pull request and the kind of change, such as
`61.fixed.md`. Open the pull request first, so you know its number. A
release collects the files into [the changelog](../reference/changelog.md),
one entry each, and links each entry to its pull request.

| kind | use it when |
| --- | --- |
| `breaking` | code that uses sharedbox has to change |
| `added` | something new: a class, a function, a keyword, an option |
| `changed` | something that already existed behaves differently |
| `deprecated` | something still works but warns, and goes in a later release |
| `removed` | something is gone |
| `fixed` | something now works as its documentation says |
| `security` | a vulnerability is closed |

`towncrier`, the tool that collects the entries at release time, writes the
file for you. Keep the text in single quotes, so the shell leaves its
backticks alone:

```bash
uv run towncrier create 61.fixed.md --content '`SharedBox.watch()`: no longer yields the same value twice.'
```

A second change of the same kind in one pull request goes in `61.fixed.2.md`.

An entry names the public symbol first, then says in a line or two what
changed. It records the change and nothing else, because the reasons belong
in the pull request. A change to something not released yet gets no entry
of its own: edit the entry that added it instead, so the release says what
users get.

A pull request nobody using sharedbox would notice, such as tests, CI, a
refactor or docs alone, needs no entry. A maintainer gives it the
`skip-changelog` label, and CI accepts it without one.
