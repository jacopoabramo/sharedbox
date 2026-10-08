---
icon: lucide/lightbulb
---

# Field types

A [field](glossary.md#field) can hold any of the types in the table below,
and most of the types you'd put in a dataclass are there. Each one is
stored in the [segment](glossary.md#segment) in a fixed number of bytes,
which is why a value never needs more room than the field set aside when
the [box](glossary.md#box) was created.

| Type | Stored as |
| --- | --- |
| `bool`, `int`, `float` | 1, 8 and 8 bytes |
| `complex` | two 64-bit floats |
| `Annotated[str, Capacity(n)]`, `bytes`, `bytearray` | a length, then up to `n` bytes |
| `Annotated[Decimal, Capacity(n)]` | the text of `str(value)`, up to `n` bytes |
| `date`, `time`, `datetime`, `timedelta` | 4, 16, 16 and 12 bytes |
| `UUID` | its 16 bytes |
| an `Enum` | the member's position |
| a `Flag` or `IntFlag` | the combined bits |
| a `Literal` | the value's position |
| an optional `X` (`Optional[X]`), a union | a byte saying which member, then that member |
| a dataclass, `NamedTuple`, tuple, `TypedDict`, attrs class, `msgspec.Struct` | its members, packed like fields |
| a `list`, `set`, `frozenset`, `dict`, `tuple[T, ...]` with `Capacity(n)` | a length, then room for `n` elements |
| an array with `Shape` and `DType` | its elements, in C order |
| a `SharedBox` subclass, optional or not | a [reference](references.md) to that box |

`NewType`, `Final`, `Annotated` and `type` aliases work too: the box looks
through them to the type they stand for.

## What the segment says about a type

For a plain `int`, the kind alone says how the bytes are laid out. For an
enum, a record or a list it doesn't, so the segment also keeps a
[description](glossary.md#description) of the type: its members, their
names, a capacity or a shape. That's what lets a C or C++ program read a
field of any type; the [segment layout](../reference/segment-layout.md#descriptions)
spells it out.

The [schema hash](glossary.md#schema-hash) covers these descriptions, so if
an enum gains a member or a record member changes type, the new class no
longer attaches to boxes made with the old one. It doesn't cover choices
that only affect what Python builds when it reads a value back: a record
class's name, `list` or `tuple[T, ...]`, `set` or `frozenset`.

## A read builds a new value

Every read turns the stored bytes into a new object. So if you change a
list, a record or an array you read from a box, you only change that
object; to store the change, assign it back. A record is rebuilt by calling
its class, which means its `__post_init__`, converters and validators run on
every read.

## Dates and times

A `datetime` or `time` is either naive or keeps a fixed offset from UTC.
If yours has a time zone, the box stores its offset at that moment, in
whole minutes, and gives it back with a `datetime.timezone` of that offset,
so a named zone such as a `ZoneInfo` comes back as a plain offset. A `time`
needs a `tzinfo` that can give an offset without a date. A `date` field
refuses a `datetime`, since storing it would lose the time of day.

## Enums and literals

An enum member is stored by its position in the class, so reading gives
you back the member itself and `is` comparisons work. Reordering the
members changes what the stored positions mean, so the schema hash changes
with it and old boxes won't attach. A `Literal` field tells values of
different types apart: in `Literal[1, True]`, `1` and `True` are two
different values.

## Unions

A union stores which of its members it holds, so a value comes back as the
type you wrote. A value whose type is exactly one member's type goes to that
member; otherwise the most specific member that takes it wins, so `bool`
comes before `int`, and `float | int` keeps `3` an `int`. Two members that
take values of the same class, such as `list[int] | list[str]`, are refused
with `TypeError` when you define the class, because the box couldn't tell
which one a stored value belongs to.

## When a field counts as changed

[`events`][sharedbox.SharedBox.events] and
[`watch`][sharedbox.SharedBox.watch] compare stored bytes, not Python
values, to decide whether a field changed. That gives a few surprises:
writing `NaN` again reports nothing, while `-0.0` after `0.0` reports a
change, and so does `True` after `1` in an `int | bool` field.

## Large values

A box has one write lock, so while a large array is copied in or out,
every other read and write of that box waits. The copy itself runs with
the GIL released, so your other threads keep going. Keep a large array in a
box of its own, so small fields don't wait behind it.
[How fast a box is](performance.md#large-values) gives the cost of the
copy, and [How to store arrays](../how-to/store-arrays.md) shows how to
read into an array you already have.
