---
icon: lucide/lightbulb
---

# Field types

A [field](glossary.md#field) can hold most of the types you'd put in a
dataclass. Each one is stored in the [segment](glossary.md#segment) in a
fixed number of bytes, which is why a value never needs more room than the
field set aside when the [box](glossary.md#box) was created. Below, each
type is shown with an example value and the exact bytes a box stores for
it, lowest address first; `|` separates the parts of a value, and you can
point at a table to read how its types are laid out:

```d2 title="What each type stores"
...@diagrams/style
grid-columns: 1
vertical-gap: 30
numbers: "numbers and text" {
  shape: sql_table
  tooltip: Numbers are little-endian. Text and bytes start with a u32 length, then take their whole capacity whatever they hold.
  "bool True": "01"
  "int 10": "0A 00 00 00 00 00 00 00"
  "float 1.5": "00 00 00 00 00 00 F8 3F"
  "complex 1+2j": "1.0 as a float | 2.0 as a float"
  "str \"été\", capacity 8": "05 00 00 00 | C3 A9 74 C3 A9 | 3 unused"
  "bytes or bytearray b\"abc\"": "03 00 00 00 | 61 62 63 | unused"
  "Decimal(\"1.50\")": "04 00 00 00 | 31 2E 35 30, the text"
}
times: "dates and times" {
  shape: sql_table
  tooltip: "A date is its day number, date.toordinal(). A time or datetime is microseconds, the UTC offset in minutes and flags (bit 0: naive, bit 1: fold), then 5 zero bytes. A UUID is its 16 bytes in network order."
  "date 2026-10-08": "39 4A 0B 00, day 739897"
  "time 09:30, naive": "00 96 7A F6 07 00 00 00 | 00 00 | 01 | 5 zeros"
  "datetime 2026-10-08 09:30 UTC": "00 D6 2B E0 50 5D 06 00 | 00 00 | 00 | 5 zeros"
  "timedelta 1 day, 30 s": "01 00 00 00 | 1E 00 00 00 | 00 00 00 00"
  "UUID 12345678-1234-...": "12 34 56 78 12 34 56 78 ..., 16 bytes"
}
choices: "choices" {
  shape: sql_table
  tooltip: An enum member or a literal value is stored by its position, counting from 0. An optional or a union starts with a byte that says which member it holds, padded to the member's alignment.
  "Enum member, 3rd": "02 00"
  "Literal value, 3rd": "02 00"
  "Flag or IntFlag A | C": "05 00 00 00 00 00 00 00"
  "int | None = 5": "01 | 7 padding | 05 00 00 00 00 00 00 00"
  "int | None = None": "00 | 7 padding | 8 zeros"
  "int | str, a union": "member number | padding | that member"
}
containers: "records, collections and arrays" {
  shape: sql_table
  tooltip: A record keeps its members at fixed offsets, like a small box. A collection starts with its length, padded to its slots' alignment, then has room for every element up to its capacity. A reference field stores which box it points at, never that box's values.
  "dataclass, NamedTuple, tuple, TypedDict, attrs, msgspec": "each member at its offset, packed like fields"
  "list[int], capacity 3 = [7, 9]": "02 00 00 00 | 4 padding | 07 00 ... | 09 00 ... | 1 unused slot"
  "set, frozenset, tuple[T, ...]": "as a list"
  "dict": "as a list, with a key and value in each slot"
  "array uint8, shape (2, 2)": "01 02 03 04, in C order"
  "SharedBox subclass": "create id | schema hash | name, 144 bytes"
}
```

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

## When a read builds a new value

A read turns the stored bytes into an object, and whether it builds a new
one each time depends on whether you could change it. A list, a dict, a
set, an array, a `TypedDict` or a record whose class isn't frozen comes back
as a new object on every read, so changing the one you hold changes nothing
in the box; to store the change, assign it back. A value you can't change,
such as a `datetime`, a `Decimal`, a `UUID`, a tuple, a frozenset, an enum
member or a record whose class is declared frozen, is built again only
after a write to its field, and until then every read returns the same
object, so `box.when is box.when` holds.

A record is built by calling its class, which means its `__post_init__`,
converters and validators run on every read of a record you can change, and
on the first read after each write of a frozen one.

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
with it and old boxes won't attach. In a `Literal` field, values of
different types count as different values even when Python would call them
equal: in `Literal[1, True]`, `1` and `True` are two separate values, and
each reads back as itself.

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
