---
icon: lucide/wrench
---

# How to store a large list

A `list` field builds one Python object per element on every read and
encodes every element again on every write, so a list of tens of thousands
of numbers costs milliseconds each way. Declare the field as
[`BoundedList`][sharedbox.BoundedList] instead, and a read copies the
stored bytes once without building any element, while assigning a
`BoundedList` to the field copies its bytes back in one step. Use it for a large list that you read or
write whole and of which you touch only a few elements. A list you always
convert to a `list` gains nothing, because the conversion decodes every
element anyway.

## 1. Declare the field as a BoundedList

A `BoundedList[T]` takes any element type a `list[T]` takes. You give the
field's [capacity](../explanation/glossary.md#capacity), a number of
elements, with [`Capacity`][sharedbox.Capacity] or
[`field`][sharedbox.field]:

```{.python}
--8<-- "docs/examples/store_a_large_list.py:declare"
```

The field is stored exactly as a `list[float]` field of the same capacity
is, so a process that declares the field as `list[float]` attaches to a box of
the same name and reads the same values.

## 2. Create the box with a BoundedList value

A type checker expects a `BoundedList` for this field, so build the initial
value with `BoundedList[float](values, capacity=n)`:

```{.python}
--8<-- "docs/examples/store_a_large_list.py:create"
```

A plain `list` or any other sequence is accepted at run time as well, and is
encoded element by element as a `list` field's value is.

## 3. Change elements and write the value back

The value you read is your own copy, as with every field. Indexing it
decodes or encodes one element, `insert`, `del` and slicing move the stored
bytes without decoding them, and assigning the value back to the field
copies its bytes in one step:

```{.python}
--8<-- "docs/examples/store_a_large_list.py:change"
```

Setting an element checks the value as a write to the field would, so a
`str` where the field holds floats raises `TypeError` at that line rather
than at the next write.

## 4. Handle a change past the capacity

A change that would leave more elements than the capacity raises
`ValueError` before it changes anything, so the value keeps every element
it had:

```{.python}
--8<-- "docs/examples/store_a_large_list.py:too-many"
```

## 5. Convert it where a real list is needed

A `BoundedList` is a `MutableSequence`, so code that takes any sequence
accepts it, but code that asks for the builtin type does not:
`isinstance(x, list)` is false, `json.dumps` refuses it, and a C extension
that reads a list's items directly does not see them. Pass `list(x)` there,
which decodes every element once:

```{.python}
--8<-- "docs/examples/store_a_large_list.py:convert"
```

## 6. Add methods with a subclass

A subclass of `BoundedList[float]` can be declared wherever a
`BoundedList[float]` can, and a read returns an instance of the subclass, so
methods that belong with the data can live on it:

```{.python}
--8<-- "docs/examples/store_a_large_list.py:subclass"
```

A read builds the value without calling the subclass's `__init__`, so an
attribute that `__init__` sets is missing on a value read from a box. Keep
what every value needs in its elements.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/store_a_large_list.py"
    ```
