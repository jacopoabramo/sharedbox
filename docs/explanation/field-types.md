---
icon: lucide/lightbulb
---

# Field types

A [field](glossary.md#field) can hold any of the types below. Each is
stored in the [segment](glossary.md#segment) in a fixed number of bytes,
so a value never needs more room than the field set aside when the
[box](glossary.md#box) was created.

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

`NewType`, `Final`, `Annotated` and `type` aliases are read through to the
type they stand for.

## What the segment says about a type

A type whose layout its kind does not fix, such as an enum, a record or a
list, has a [description](glossary.md#description) in the segment: its
members, names, capacity or shape. C and C++ programs read a field of any
type through it; see the
[segment layout](../reference/segment-layout.md#descriptions). The
[schema hash](glossary.md#schema-hash) covers these descriptions, so a
class whose enum gains a member, or whose record changes a member's type,
no longer attaches to boxes made with the old one. It does not cover how
Python reads a value back: a record class's name, `list` against
`tuple[T, ...]`, or `set` against `frozenset`.

## A read builds a new value

Every read decodes the stored bytes into a new object. Changing a list,
a record or an array read from a box changes only that object; assign it
back to store it. A record is rebuilt by calling its class, so its
`__post_init__`, converters and validators run on every read.

## Dates and times

A `datetime` or `time` is naive or has a fixed offset from UTC. An aware
value is stored by its offset at that moment, in whole minutes, and comes
back with a `datetime.timezone` of that offset: a named zone such as a
`ZoneInfo` is not kept. A `time` needs a `tzinfo` that gives an offset
without a date. A `date` field refuses a `datetime`, which would lose its
time of day.

## Enums and literals

An enum member is stored by its position in the class, so reading gives
back the member itself and `is` comparisons work. Changing the order of
members changes what stored positions mean, and the schema hash changes
with it. A `Literal` field keeps the value and its type apart: in
`Literal[1, True]`, `1` and `True` are two values.

## Unions

A union stores which member it holds. A value whose type is exactly one
member's type goes to that member; otherwise the most specific member that
takes it is used, so `bool` comes before `int`. `float | int` keeps `3`
an `int`. Two members that take values of the same class, such as
`list[int] | list[str]`, raise `TypeError` when the class is defined,
since the stored value could not tell them apart.

## When a field counts as changed

[`events`][sharedbox.SharedBox.events] and
[`watch`][sharedbox.SharedBox.watch] report a change when a field's
stored bytes change. Writing `NaN` again reports nothing, `-0.0` after
`0.0` reports a change, and so does `True` after `1` in an `int | bool`
field.

## Large values

A box has one write lock. While a large array is copied in or out, every
other read and write of that box waits, and the copy runs with the GIL
released. [How fast a box is](performance.md#large-values) gives the copy
cost; a large array is best kept in a box of its own.
