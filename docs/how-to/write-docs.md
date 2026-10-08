---
icon: lucide/pen-line
---

# How to write documentation

Good documentation reads as if someone who knows `sharedbox` were sitting
next to you and explaining it. These rules are how the pages get there.
They apply to every page under `docs/` and to every docstring, since the
API reference pages are made from docstrings.

## Write for a reader who is new to this

Picture a reader around 15 years old who knows some Python and nothing
about shared memory, and write as if you were explaining in person.

- Talk to the reader as "you", and name who does what: "you assign the
  field", "the watcher calls the callback". Active voice, not "the callback
  is called".
- Open each section and each paragraph with its point, framed as what the
  reader gets out of it. Start a sentence from something the reader
  already knows and end it on the new part, so each sentence picks up where
  the last one stopped.
- Keep most sentences short but vary their length, and join ideas that
  depend on each other with "so", "because" or "which means" instead of
  leaving a list of separate facts. Contractions are fine.
- Say each point once. Don't open a paragraph with a label ("The cache is
  the store") before saying what the thing does.
- Put a catch the reader can run into in a warning box. Its title names
  what goes wrong, and its last sentence says what to do:

    ```markdown
    !!! warning "Callbacks run on another thread"
        A callback runs on the watcher thread, not on your main thread. ...
        Connect it with `thread="main"` to run it on your main thread.
    ```

- Show a short code example when it is clearer than a paragraph.

| stiff | friendly |
| --- | --- |
| `close` detaches this process's handle. | `close` lets go of the box in your process, and the other processes keep it. |
| A value longer than the capacity raises `ValueError`. | If you assign a value longer than the capacity, you get a `ValueError` and the field keeps its old value. |
| Following moves to the new box shortly after the assignment. | A background thread switches to the new box shortly after you assign it, so a write made before then is missed. |

## Define each term once

Every technical word, such as box, segment, capacity or waiter slot, has
one plain definition in the [glossary](../explanation/glossary.md). A page
links the word to its glossary entry the first time it uses it:

```markdown
A [box](../explanation/glossary.md#box) lives in one segment.
```

Never write a second definition somewhere else. If a word needs explaining
and is not in the glossary yet, add it there first.

Acronyms such as PID and ABI also go in `includes/abbreviations.md`, which
turns them into tooltips on every page. Keep that file to acronyms and rare
words, since it underlines every place the word appears.

## Say the thing itself

Write the claim, not a figure of speech for it. "Load-bearing", "footgun",
"first-class", "plumbing" and "tripwire" only make sense to people who
already know the jargon. Write what they stand for: "cannot change without
breaking X", "easy to misuse", "fully supported", "the code that connects X
to Y".

Other rules:

- Library and package names are code spans every time you mention them,
  `sharedbox` included: `psygnal`, `numpy`, `nanobind`.
- Headings name the topic in a few words ("Unexpected exits"); a question
  works on a page of limits. Steps in a how-to or tutorial say what the
  reader does ("Add the field").
- No em dashes or en dashes. Use a hyphen, a comma, or two sentences.
- Write arrows as `->`, not as a special character.
- No sales words ("powerful", "seamless") and no closing summary sentence.

Before you open the pull request, read each changed page and docstring
once more against these rules, and fix what you find: inflated claims,
sales words, vague sources, lists of three made up for rhythm, filler
phrases and dashes.

## Put the page in the right section

| section | the reader wants to | example |
| --- | --- | --- |
| Tutorials | learn by building something, step by step | sharing a record between processes |
| How-to Guides | get one task done, including contributing | how to name a box |
| Explanations | understand how and why | how a box is stored, limits |
| Reference | look up a fact | an API page, the glossary, the segment layout, the changelog |

Write each fact once, on the page where it belongs, and link to it from the
others. A link from a page to a file outside `docs/` is a full GitHub URL,
`https://github.com/jacopoabramo/sharedbox/blob/main/<path>`, since the site
holds only `docs/`.

## Write docstrings

The API reference pages are made from the docstrings, so what a public
class, function or method does is written in its docstring and nowhere else.
Fix a wrong API page in the docstring, not in the `.md` file.

- Use the [numpydoc](https://numpydoc.readthedocs.io/en/latest/format.html)
  format: a one-line summary, a blank line, an optional longer
  description, then only the sections that add something.
- Leave types out when the signature has annotations. The page shows them
  from the signature.
- Link to another object with a Markdown cross-reference, never a
  reStructuredText role such as `:class:`:

```markdown
Open it with [`attach`][sharedbox.SharedBox.attach].
```

`ruff` checks the docstring format of the modules in `src/sharedbox`; see
[How to run the commit checks](run-commit-checks.md).

## Show code from a script

Where it can, the Python code of a tutorial or a how-to guide lives in a
script that the tests run, and the page includes parts of it, so the page
shows what runs.

- The three tutorials build one script, `docs/tutorials/motor.py`.
- Each how-to guide that shows Python has a script of its own in
  `docs/examples/`, named after the guide: `name_a_box.py` for
  `name-a-box.md`.

Mark each part of the script with a `start` and an `end` comment, and
include it as `docs/how-to/name-a-box.md` does. Write the fence of an
included part as `{.python}`. `ruff format` reads the line inside a
`python` fence as Python and rewrites it, and the page then shows that line
in place of the code. The last tutorial ends with the whole script in a
collapsed block.

The scripts are type checked with the rest of the code by
`uv run tox -e mypy`, and the tests run them:

- `tests/test_doc_tutorials.py` runs `motor.py` and compares what it prints
  with the output the tutorial pages show. When you change what the script
  prints, change the pages and the test together.
- `tests/test_doc_examples.py` runs each script in `docs/examples/`.

### Name the boxes in a script

Every [box](../explanation/glossary.md#box) a tutorial script creates has a
name starting with `tutorial-`, and every box an example script creates has
a name starting with `example-`. Before running a script, the tests add
the `pytest` run's own label after the prefix, so two runs at the same time
never create a box under the same name. A box named without the prefix is
left as it is, and two runs at the same time then use the same segment.

`tests/test_doc_tutorials.py` also lists the names the tutorial script
uses, to remove their segments on Linux after the run. Add a new
`tutorial-` name there.

## Check the build

```bash
uv run tox -e docs
```

This builds the site and then checks that every cross-reference found its
target, every included script part was read, and every link to a part of
a page reaches it. See [How to build the docs](build-docs.md).
