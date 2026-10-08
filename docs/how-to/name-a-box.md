---
icon: lucide/wrench
---

# How to name a box

A second process finds a [box](../explanation/glossary.md#box) by its name, so every box needs
one. You can let the class pick the name for you, give the class a fixed
name, or name each box when you create it. Pick the first unless one of the
others fits better: it needs no setup and works in every process that
imports the class.

## Let the class name the box

When you call the class, it creates a box under a name it works out
itself, and [`attach`][sharedbox.SharedBox.attach] with no name opens that
same box:

```{.python}
--8<-- "docs/examples/name_a_box.py:class-name"
```

The name comes from the class's [identity](../explanation/glossary.md#identity): normally where
the class is defined, `module.qualname`, or the `identity` keyword if the
class sets one, as `Motor` does here. Every process that imports the class
works out the same name, so you never have to pass it around.

Set `identity` when you might move the class to another module or rename
it. With `identity` set, moving or renaming the class doesn't change its
identity, so the box keeps its name and running processes still find it.

!!! warning "Changing the identity cuts off running processes"
    A new identity gives the box a new name and a new
    [schema hash](../explanation/glossary.md#schema-hash), so a process still running the old code
    can no longer attach. Change it only when you can restart every process
    that uses the box.

## Give the class a fixed name

If you'd rather see a name you recognise, set the `name` keyword on the
class:

```{.python}
--8<-- "docs/examples/name_a_box.py:fixed-name"
```

A readable name is easier to spot, for example when you list `/dev/shm` on
Linux to see which boxes exist. [`SharedBox`][sharedbox.SharedBox] lists the
characters a name may use.

## Name each box when you create it

When you need several boxes of the same class, say one per motor, give
each its own name when you create it with
[`create`][sharedbox.SharedBox.create], and pass the same name to `attach`:

```{.python}
--8<-- "docs/examples/name_a_box.py:each-box"
```

After the name, `create` takes the [field](../explanation/glossary.md#field) values just like
calling the class does.

## If the name is taken

You can't create a box under a name that is already in use; you get
[`SegmentExistsError`][sharedbox.SegmentExistsError] instead:

```{.python}
--8<-- "docs/examples/name_a_box.py:taken"
```

The message tells you why the name is taken: either the process that
created it is still running, or, on Linux, the box was left behind by a
process that crashed. Pick another name, or remove the old box as
[How to clean up segments](clean-up-segments.md#remove-a-segment-left-by-a-crash)
shows.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/name_a_box.py"
    ```
