---
icon: lucide/wrench
---

# How to store a large set or dict

A `set` or `dict` field builds one Python object per element on every read
and encodes every element again on every write, so a set or dict of tens
of thousands of keys costs milliseconds each way. Declare a set field as
[`BoundedSet`][sharedbox.BoundedSet] instead, and a read copies the stored
bytes without building any element, while assigning a `BoundedSet` to the
field copies its bytes back in one step. An `in` test encodes only the key
it looks for and compares it with the stored bytes.

After a read, the first 64 calls to `in`, `add`, `discard` or `remove`
compare the key with every element. In a set of more than 8 elements, the
65th call hashes every element once into a table that the value keeps in
its own memory, not in the box. From then on each test or change looks up
only the one key. A dict field gets the same from a `BoundedDict`, as
[step 9](#9-store-a-large-dict-as-a-boundeddict) shows.

How much that saves depends on the size and on what you do with the value.
In `benchbox ops` on Windows with CPython 3.11, reading a set and testing
one key with `in` takes about 1.1 times as long as with a `set` field at 16
elements, and is about 30 times faster at 1024 and about 100 times faster
at 65536. Reading a set, changing one key and writing it back is about 1.4
times faster at 16 elements, 18 times faster at 1024 and 40 times faster
at 65536. Going through every element decodes each one, so it takes about 1.5
times as long as with a `set` at 16 elements, about as long at 1024, and is
about 1.5 times faster at 65536. So use it for a large set that you read or
write whole and test or change a few keys at a time. A set you always
convert to a builtin `set` gains little, because the conversion decodes
every element anyway.

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
value with `in` returns `False`, since the set cannot hold it. That holds
for `1.0` and `True` in a set of integers too, although Python counts both
equal to `1`, because a key of another type is never encoded.

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
operator. A type checker does not follow that run-time step: it finds
that `set.__or__` accepts any set and is declared to return a `set`, so it
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

## 9. Store a large dict as a BoundedDict

A [`BoundedDict[K, V]`][sharedbox.BoundedDict] does for a `dict[K, V]`
field what a `BoundedSet` does for a set: a read copies the stored bytes,
assigning it back copies them in one step, and a lookup encodes only the
key it looks for. Declare and create it as the set above, with the
capacity counting key and value pairs:

```{.python}
--8<-- "docs/examples/store_a_large_set_or_dict.py:dict-declare"
```

`d[key]`, `d.get(key)` and `key in d` behave as they do on a `dict`,
except that a key of another type is never found, as step 3 says for a
set. Its keys follow the rules of step 6: the declaration refuses the same
key types, and a nan, a NaT, or a `datetime` or `time` with a time zone
raises `ValueError` when it is added as a key. The values can be of any
type a `dict` field holds, and a process that declares the field as
`dict[int, float]` attaches to the same box and reads the same items.

A dict gains less than a set from this at the largest size. In
`benchbox ops` on Windows with CPython 3.11, against a `dict[int, float]`
field, reading a
`BoundedDict[int, float]` and testing one key with `in` is about 2 times
faster at 16 items, 18 times faster at 1024 and 14 times faster at 65536.
Reading it, deleting one key, adding it back and writing it back is about
2.3 times faster at 16 items, 10 times faster at 1024 and 16 times faster
at 65536. Going through every key is about 1.2 times faster at 16 items,
2 times faster at 1024 and 2.5 times faster at 65536.

A `BoundedDict` is a `MutableMapping` but not a `dict`, so pass `dict(d)`
where code asks for the builtin type, as step 7 does for a set.

## 10. Change items in insertion order

A `BoundedDict` keeps its items in insertion order, as a `dict` does.
Assigning to a key it holds replaces the value where it is, a new key goes
last, and `del` leaves the other items in the order they had:

```{.python}
--8<-- "docs/examples/store_a_large_set_or_dict.py:dict-change"
```

Replacing a value is quick, but a delete takes time in proportion to the
size of the dict. It moves every later item down one place, and when the
dict has more than 8 items and has built its table of keys, the next
lookup builds the table again.

A change that would leave more keys than the capacity raises
`ValueError`, as for the set, while replacing a value in a full dict
always works.

## 11. Compare values as Python compares them

`==` compares a `BoundedDict` with any mapping by its keys, then compares
the values with `==`, so two values that Python counts as equal match
whatever their stored bytes. A float nan is equal to nothing, itself
included, and a read decodes a new float object each time, so a dict
holding a nan value is not equal even to its own copy:

```{.python}
--8<-- "docs/examples/store_a_large_set_or_dict.py:dict-nan"
```

A builtin `dict` holding the same nan object in both would compare equal,
because Python checks whether two values are the same object before it
calls `==`.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/store_a_large_set_or_dict.py"
    ```
