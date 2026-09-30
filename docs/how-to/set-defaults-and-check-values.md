---
icon: lucide/wrench
---

# How to set defaults and check values

A `SharedBox` subclass takes its [field](../explanation/glossary.md#field)
values as a dataclass does, and the same tools set defaults and check
values: [`field`][sharedbox.field], `dataclasses.KW_ONLY`,
`dataclasses.InitVar` and `__post_init__`.

## Give a field a default

A plain value after `=` is the default. For a value computed each time a
box is created, use `field(default_factory=...)`:

```{.python}
--8<-- "docs/examples/set_defaults_and_check_values.py:defaults"
```

The factory's result is stored like any other value, so it must be one of
the types a box holds: a factory cannot give a field a list.

## Make fields keyword-only

Fields after a `KW_ONLY` annotation must be passed by name:

```{.python}
--8<-- "docs/examples/set_defaults_and_check_values.py:keyword-only"
```

For every field of a class, pass `kw_only=True` in the class statement
instead; for one field, use `field(kw_only=True)`. A keyword-only field
with a default may come before a positional field without one.

## Check values when the box is created

Define `__post_init__` and raise when a value is wrong:

```{.python}
--8<-- "docs/examples/set_defaults_and_check_values.py:check"
```

`__post_init__` runs after the values are written and before any other
process can open the box. When it raises, the box is removed without ever
being published, so no process sees the wrong values and the name is free
again. The docstring of [`SharedBox`][sharedbox.SharedBox] has the details.

`__post_init__` runs only when the box is created, not when a field is
assigned later, and not in a process that
[attaches][sharedbox.SharedBox.attach] the box.

## Take an argument that is not stored

An `InitVar` field is a constructor argument that `__post_init__` receives
and the box does not store:

```{.python}
--8<-- "docs/examples/set_defaults_and_check_values.py:init-var"
```

## Describe a field

`metadata` and `doc` stay in Python and are never stored in the box.
Read them back with [`fields`][sharedbox.fields]:

```{.python}
--8<-- "docs/examples/set_defaults_and_check_values.py:describe"
```

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/set_defaults_and_check_values.py"
    ```
