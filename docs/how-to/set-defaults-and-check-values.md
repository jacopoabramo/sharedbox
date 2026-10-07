---
icon: lucide/wrench
---

# How to set defaults and check values

If you know dataclasses, you already know how to set defaults and check
values on a box. A `SharedBox` subclass takes its [field](../explanation/glossary.md#field) values
the way a dataclass does, and it understands the same tools:
[`field`][sharedbox.field], `dataclasses.KW_ONLY`, `dataclasses.InitVar`
and `__post_init__`.

## Give a field a default

A plain value after `=` is the default. If the default has to be worked out
each time a [box](../explanation/glossary.md#box) is created, such as the current time, use
`field(default_factory=...)`:

```{.python}
--8<-- "docs/examples/set_defaults_and_check_values.py:defaults"
```

The factory's result is stored like any other value, so it has to be a type
the field can hold.

## Make fields keyword-only

Fields after a `KW_ONLY` annotation have to be passed by name, which keeps
long constructor calls readable:

```{.python}
--8<-- "docs/examples/set_defaults_and_check_values.py:keyword-only"
```

To make every field of a class keyword-only, pass `kw_only=True` in the
class statement instead, and for a single field use `field(kw_only=True)`.
A keyword-only field with a default may come before a positional field
without one.

## Check values when the box is created

Define `__post_init__` and raise an exception when a value is wrong:

```{.python}
--8<-- "docs/examples/set_defaults_and_check_values.py:check"
```

`__post_init__` runs after the values are written but before any other
process can open the box. If it raises, the box is removed before anyone
sees it, so no process reads the wrong values and you can use the name
again straight away. The docstring of [`SharedBox`][sharedbox.SharedBox]
has the details.

!!! warning "Later writes are not checked"
    `__post_init__` runs once, when the box is created. It doesn't run when
    you assign a field later, or in a process that
    [attaches][sharedbox.SharedBox.attach] the box. Check later values where
    you write them.

## Take an argument that is not stored

Sometimes the constructor needs a value only to work out the fields, not to
keep. Declare it as an `InitVar`: `__post_init__` receives it, and the box
doesn't store it:

```{.python}
--8<-- "docs/examples/set_defaults_and_check_values.py:init-var"
```

## Describe a field

You can attach notes to a field with `metadata` and `doc`. They stay in
Python and are never stored in the box, and you read them back with
[`fields`][sharedbox.fields]:

```{.python}
--8<-- "docs/examples/set_defaults_and_check_values.py:describe"
```

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/set_defaults_and_check_values.py"
    ```
