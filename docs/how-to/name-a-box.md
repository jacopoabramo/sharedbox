---
icon: lucide/wrench
---

# How to name a box

Every [box](../explanation/glossary.md#box) lives in a
[segment](../explanation/glossary.md#segment) with a name, and a second
process opens the box by that name. You can let the class choose the name,
give the class a fixed name, or name each box when you create it.

## Let the class name the box

Calling the class creates a box under the class's name, and
[`attach`][sharedbox.SharedBox.attach] without a name opens it:

```{.python}
--8<-- "docs/examples/name_a_box.py:class-name"
```

The name is a hash of the class's
[identity](../explanation/glossary.md#identity), which is its
`module.qualname` unless the class sets the `identity` keyword, as `Motor`
does here. Every process that imports the class computes the same name, so
none of them has to pass it around.

Set `identity` when the class may move to another module or get another
name: the identity stays, and so does the name of the box. Changing the
identity itself gives a new name and a new
[schema hash](../explanation/glossary.md#schema-hash), and processes that
still use the old one cannot attach.

## Give the class a fixed name

The `name` keyword replaces the hash with a name you choose:

```{.python}
--8<-- "docs/examples/name_a_box.py:fixed-name"
```

A readable name is easier to find, for example in `/dev/shm` on Linux.
[`SharedBox`][sharedbox.SharedBox] lists the characters a name may hold.

## Name each box when you create it

When one class describes several boxes, give each its own name with
[`create`][sharedbox.SharedBox.create], and pass the same name to
`attach`:

```{.python}
--8<-- "docs/examples/name_a_box.py:each-box"
```

`create` takes the field values after the name, as calling the class does.

## If the name is taken

Creating a box under a name that is already in use raises
[`SegmentExistsError`][sharedbox.SegmentExistsError]:

```{.python}
--8<-- "docs/examples/name_a_box.py:taken"
```

The message says why the name is taken: its creator may still be running,
or on Linux the box may be left over from a process that crashed. Choose
another name, or remove the old box as
[How to clean up segments](clean-up-segments.md#remove-a-segment-left-by-a-crash)
shows.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/name_a_box.py"
    ```
