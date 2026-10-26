---
icon: lucide/wrench
---

# How to store a large set

A `set` field builds one Python object per element on every read and
encodes every element again on every write, so a set of tens of thousands
of keys costs milliseconds each way. Declare the field as
[`BoundedSet`][sharedbox.BoundedSet] instead, and a read copies the stored
bytes without building any element, while assigning a `BoundedSet` to the
field copies its bytes back in one step. An `in` test encodes only the key
it looks for and compares it with the stored bytes. Up to 8 elements it
compares every element. Above that, the first test after a read hashes
every element into a table the value keeps in its own memory, not in the
box, and every later test looks up only the one key.

Use it for a large set that you read or write whole and test for a few
keys at a time. Going through every element decodes each one, and so does
converting the value to a builtin `set`, so a set you always iterate or
convert gains little.

## 1. Declare the field as a BoundedSet

A `BoundedSet[T]` is declared as a `set[T]` field is, with the field's
[capacity](../explanation/glossary.md#capacity) in elements given through
[`Capacity`][sharedbox.Capacity] or [`field`][sharedbox.field]:

```{.python}
--8<-- "docs/examples/store_a_large_set_or_dict.py:declare"
```

The field is stored exactly as a `set[int]` field of the same capacity is,
so a process that declares the field as `set[int]` attaches to a box of the
same name and reads the same values.

## 2. Create the box with a BoundedSet value

A type checker expects a `BoundedSet` for this field, so build the initial
value with `BoundedSet[int](values, capacity=n)`:

```{.python}
--8<-- "docs/examples/store_a_large_set_or_dict.py:create"
```

A builtin `set`, a `frozenset` or any other set is accepted at run time as
well, and is encoded element by element as a `set` field's value is.

## 3. Change elements and write the value back

The value you read is your own copy, as with every field. `in`, `add`,
`discard` and `remove` encode one key, the last three change the stored
bytes, and assigning the value back to the field copies those bytes in one
step:

```{.python}
--8<-- "docs/examples/store_a_large_set_or_dict.py:change"
```

Adding a key checks it as a write to the field would, so a `str` where the
field holds integers raises `TypeError` at that line. Testing for such a
value with `in` returns `False`, since the set cannot hold it.

## 4. Combine it with other sets

The operators `|`, `&`, `-` and `^` and their in-place forms take any set,
builtin or bounded, and return a `BoundedSet` of the same class and
capacity, whichever side it is on. The comparisons `<=`, `<`, `>=` and `>`
take any set as well:

```{.python}
--8<-- "docs/examples/store_a_large_set_or_dict.py:combine"
```

An element of the other set that the field cannot hold, such as a `str`
in a set of integers, is absent for `&`, for `value - other` and for the
comparisons. `|`, `^` and `other - value` raise `TypeError` for it, since
the result would hold it.

At run time `{1} | value` returns a `BoundedSet`, because the builtin
`set` declines to combine with it and Python then calls the `BoundedSet`
operator. A type checker reads only the builtin `set`'s annotation, so it
types the result as `set`.

## 5. Handle a change past the capacity

A change that would leave more elements than the capacity raises
`ValueError` before it changes anything, so the value keeps every element
it had:

```{.python}
--8<-- "docs/examples/store_a_large_set_or_dict.py:too-many"
```

The change stops at the first new key that does not fit, so the message
counts the capacity plus one, which can be lower than the number of keys
the change would have added.

## 6. Choose an element type it can compare

A `BoundedSet` finds a key by comparing stored bytes, so its element type
must store two equal values as the same bytes. The declaration raises
`TypeError` for a type that would not, such as a `Decimal`, a `frozenset`,
a union of two number types or of a `StrEnum` and `str`, or a dataclass
with a field declared `field(compare=False)`:

```{.python}
--8<-- "docs/examples/store_a_large_set_or_dict.py:refused"
```

Declare such a field as a builtin `set` instead. Some values are refused
when they are added rather than when the field is declared: a float nan,
a NumPy `datetime64` NaT, and a `datetime` or `time` with a time zone raise
`ValueError`, and `-0.0` is stored as `0.0`. A dataclass or other record
type that defines its own `__eq__` is accepted, but the set still compares
all of an element's stored bytes, so it may hold two elements that
`__eq__` counts as equal.

## 7. Convert it where a real set is needed

A `BoundedSet` is a `MutableSet`, so code that takes any set accepts it,
but code that asks for the builtin type does not: `isinstance(x, set)` is
false, and a C extension that reads a set's items directly does not see
them. A `BoundedSet` cannot be hashed either, so it is no key of a `dict`.
Pass `set(x)` or `frozenset(x)` there, which decodes every element once:

```{.python}
--8<-- "docs/examples/store_a_large_set_or_dict.py:convert"
```

A `BoundedSet` iterates in the order its elements are stored, which may
differ from the order of a builtin `set` holding the same elements.

## 8. Add methods with a subclass

A subclass of `BoundedSet[int]` can be declared wherever a
`BoundedSet[int]` can, and a read returns an instance of the subclass, so
methods that belong with the data can live on it:

```{.python}
--8<-- "docs/examples/store_a_large_set_or_dict.py:subclass"
```

A read builds the value without calling the subclass's `__init__`, so an
attribute that `__init__` sets is missing on a value read from a box. Keep
what every value needs in its elements.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/store_a_large_set_or_dict.py"
    ```
