# API reference

Everything below is importable from `sharedbox`, including
`SupportsSharedBox` and `get_include()` (see
[`__sharedbox_box__`](#__sharedbox_box__-for-library-authors)).

## When to use `SharedBox`

`SharedBox` is for sharing a record between processes. Every read and write
encodes the value to or from bytes in shared memory, which costs a few
hundred nanoseconds and means a read returns a copy.

Threads of one process can share ordinary Python objects instead, which is
several times faster and keeps any Python type. psygnal's `evented`
dataclasses give such an object the same `events` interface as a box; a
`threading.Lock` makes several changes appear at once, as `update()` does:

```python
import threading
from dataclasses import dataclass

from psygnal import evented


@evented
@dataclass
class Motor:
    position: int = 0
    enabled: bool = False


motor = Motor()
lock = threading.Lock()
motor.events.position.connect(lambda new, old: print(old, "->", new))
with lock:
    motor.position = 10              # prints: 0 -> 10
```

Unlike a box's signals, these callbacks run on the thread that changes the
field, before the assignment returns.

## `SharedBox`

```python
class SharedBox:
    def __init_subclass__(cls, *, name: str | None = None, kw_only: bool = False,
                          lock_timeout: float | None = None, max_waiters: int | None = None,
                          identity: str | None = None) -> None: ...
    def __init__(self, *args, **kwargs) -> None: ...
    @classmethod
    def create(cls, name: str, /, *args, **kwargs) -> Self: ...
    @classmethod
    def attach(cls, name: str | None = None) -> Self: ...
    name: str                    # read-only
    closed: bool                 # read-only
    events: SignalGroup          # read-only
    def update(self, **values) -> None: ...
    def snapshot(self, *, follow: bool = False) -> dict[str, Any]: ...
    def watch(self, field: str) -> FieldWatch: ...
    def force_unlock(self) -> None: ...
    def close(self) -> None: ...
    unlink                       # MyBox.unlink(name=None) or box.unlink()
    def __sharedbox_box__(self, max_version: tuple[int, int] | None = None,
                          **kwargs) -> CapsuleType: ...
```

Base class for a record whose annotated fields live in one named
shared-memory segment. Every process that opens the segment reads and writes
the same values.

### Subclassing and field types

```python
from dataclasses import KW_ONLY
from typing import Annotated

from sharedbox import Capacity, SharedBox


class Stage(SharedBox):
    x: float
    y: float
    label: Annotated[str, Capacity(32)] = "stage"
    _: KW_ONLY
    moving: bool = False
    raw: Annotated[bytes, Capacity(16)] = b""


stage = Stage(0.0, 1.5, moving=True)
print(stage)  # Stage(x=0.0, y=1.5, label='stage', moving=True, raw=b'')
stage.close()
Stage.unlink()
```

A field is a public annotation with one of these types:

| Annotation | Stored as |
| --- | --- |
| `bool` | 1 byte |
| `int` | signed 64-bit integer; a larger value raises `OverflowError` |
| `float` | 64-bit float; an `int` is accepted and converted |
| `Annotated[str, Capacity(n)]` | UTF-8, at most `n` bytes |
| `Annotated[bytes, Capacity(n)]` | at most `n` bytes; `bytearray` and `memoryview` are accepted |
| `X` or `X \| None`, where `X` is a `SharedBox` subclass | which box of `X` the field refers to; see [Reference fields](#reference-fields) |

Names starting with `_` and `ClassVar` annotations are not fields. Any other
annotation raises `TypeError` when the class is defined, as does a class
with no fields or more than 256. Fields of a base class come first.

A field named like a `SharedBox` member (`name`, `closed`, `close`,
`unlink`, `update`, `snapshot`, `watch`, `events`, `force_unlock`, `create`,
`attach`) raises `TypeError` when the class is defined.

Fields are positional by default, in declaration order, as in a dataclass.
Fields declared after a `dataclasses.KW_ONLY` annotation in the same class
are keyword-only. A default is checked when the class is defined, and a
positional field without a default cannot follow one with a default.

Assigning a value of the wrong type raises `TypeError`, and a `str` or
`bytes` value longer than its capacity raises `ValueError`. Either way the
stored value does not change. Assigning to a name that is not a field raises
`AttributeError`, and so does deleting a field
(`AttributeError: a SharedBox field cannot be deleted`).

A box has no `__dict__`: every subclass gets empty `__slots__` unless it
declares its own. A subclass's `__slots__` may not name `_segment`, which
`SharedBox` uses; that raises `TypeError` when the class is defined.

Two boxes compare equal only if they are the same object.

### `field()`, `InitVar` and `__post_init__`

```python
from dataclasses import InitVar
from typing import Annotated

from sharedbox import Capacity, SharedBox, field, fields


class Motor(SharedBox):
    position: int
    label: Annotated[str, Capacity(32)] = field(default="", repr=False)
    limit: int = field(default=100, kw_only=True, metadata={"unit": "mm"})
    offset: InitVar[int] = 0

    def __post_init__(self, offset: int) -> None:
        self.position += offset


motor = Motor(10, "x-axis", offset=5)
print(motor)  # Motor(position=15, limit=100)
print(fields(Motor)[2].metadata["unit"])  # mm
motor.close()
Motor.unlink()
```

`field()` sets the options of one field, as `dataclasses.field` does;
[`field` and `fields`](#field-and-fields) lists them. A plain class
attribute is still the field's default.

A `dataclasses.InitVar[T]` annotation declares a constructor argument
that is not stored. It takes a position like a field and may have a
default, given as a class attribute or with `field(default=...)`; it
takes no `default_factory` and no `init=False`. It is not in the
layout, the schema hash, `fields()`, `snapshot()` or `events`. A class
with an `InitVar` and no `__post_init__` raises `TypeError` when it is
defined. An `InitVar` with the name of an inherited field removes that
field from the subclass, as in `dataclasses`; reading or assigning the
name on a box raises `AttributeError`.

`__post_init__(self, *initvars)` runs when the class is called and when
`create()` is used, after the values are written, with each `InitVar`
value in declaration order. It may read and assign fields; its writes
go into the segment. It does not run for `attach()` or unpickling,
which open a box that already exists.

No other process sees the box before `__post_init__` returns: the box
is published after it. Until then its name is taken: creating another
box under it raises `SegmentExistsError`, whose message says the box is
being created by a pid that is still running. `attach()` waits at most
1 s (the lock timeout, if shorter) and then raises
`SegmentNotFoundError`, so a `__post_init__` that takes longer makes
those attaches fail, including an `attach()` made from `__post_init__`
itself. If `__post_init__` raises, the box is closed and
its name removed without ever being published, and the exception
propagates.

A box cannot be handed to other extensions until `__post_init__`
returns: `__sharedbox_box__()` raises `BufferError` before then.
Pickling the box inside `__post_init__` works, but the pickle can only
be loaded once the box is published.

### Class keywords

```python
from sharedbox import SharedBox


class Settings(SharedBox, name="app-settings", kw_only=True, lock_timeout=1.0):
    rate: float
    retries: int = 3


settings = Settings(rate=20.0)
print(settings.name, settings.retries)  # app-settings 3
settings.close()
Settings.unlink()
```

- `name`: the segment name for boxes made by calling the class. By default
  it is 16 hex digits of SHA-256 over the class's identity (see
  `identity`), so every process that imports the class uses the same name.
  A name matches `[A-Za-z0-9_.-]{1,128}`; any other raises `ValueError`.
- `kw_only`: make every field this class declares keyword-only. Inherited
  fields keep the setting of the class that declares them.
- `lock_timeout`: seconds a read or write waits for a write in progress
  before `LockTimeoutError`, 5.0 by default. Must be finite and in
  `(0, 86400]`; any other value raises `ValueError`. On Windows a wait
  ends on a timer tick, so a timeout can expire late by up to one timer
  tick, 15.6 ms at the default Windows timer resolution.
- `identity`: a non-empty string, by default the class's `module.qualname`
  (with `__mp_main__` read as `__main__`); anything else raises
  `TypeError`. It enters the schema hash and names the box when `name` is
  not given. A subclass does not inherit it. Two classes with the same
  identity and fields share a box, even when they live in different
  modules. Changing the identity, for example from `"motor/1"` to
  `"motor/2"`, makes processes that still use the old one fail to attach.
- `max_waiters`: 1 to 4096, default 64; any other value raises
  `ValueError`. Each box handle whose `watch()` or `events` is in use
  holds one waiter slot, counted across every process. A watcher that
  finds every slot taken logs a warning to the `sharedbox` logger and
  checks for changes once a second until a slot is free.

```python
from sharedbox import SharedBox


class Frame(SharedBox, identity="camera/frame/1", max_waiters=16):
    exposure: float = 0.01
    count: int = 0


with Frame() as frame:
    print(len(frame.name))  # 16
Frame.unlink()
```

### Creating and attaching

Calling the class creates the segment under the class's name and writes the
given values. `create(name, ...)` does the same under another name, so one
class can describe several boxes. Both raise `SegmentExistsError` if the name is
taken. Its message says which case it is:

- the box's creator is still running, with its pid;
- the box is still being created: its creator is running and has not
  returned from `__post_init__` yet. The message gives its pid and says to
  wait for it or use another name;
- the creator runs in another pid namespace, such as another container;
- the creator is no longer running. On Linux the box is then probably left
  over from a crash, and `Box.unlink(name)` removes it. On Windows a
  process, possibly this one, still has the box open, and the name is
  freed when every handle to it is closed;
- the name holds no published box. On Linux it may be left over from a
  crash during create, and `Box.unlink(name)` removes it. On Windows a
  process, possibly this one, still has something open under that name,
  and the name is freed when every handle to it is closed.

When none of these can be told, the message only says the name is taken.
Nothing is removed automatically: other processes may still use a box
whose creator has exited.

`attach(name=None)` opens an existing segment, by default the one named
after the class. It raises `SegmentNotFoundError` if there is none, or if
the shared memory under that name does not become a box within 1 s (the
lock timeout, if shorter). It raises `SchemaMismatchError` if the segment
was made by a different class or a different version of this class, or
uses another major version of the segment layout, or has a field of a kind
this version cannot read.

```python
from sharedbox import SharedBox


class Counter(SharedBox):
    value: int = 0


main = Counter()
spare = Counter.create("counter-2", 5)
other = Counter.attach()
other.value += 1
print(main.value, Counter.attach("counter-2").value)  # 1 5
for box in (main, spare, other):
    box.close()
Counter.unlink()
Counter.unlink("counter-2")
```

`name` is the segment name, to pass to `attach()` in another process.

### `update` and `snapshot`

`update(**values)` writes several fields under one lock: a reader sees all
of the new values or none of them. It checks every value before it writes
anything. `snapshot()` returns every field's value, read at one point in
time. A reference field appears in it as a [`BoxRef`](#boxref), or `None`
when it is empty.

`snapshot(follow=True)` replaces each reference with the snapshot of the
box it refers to, itself taken with `follow=True`. Each box is read at its
own moment, not together with the others. A box that the same call has
already read stays a `BoxRef`: a second field that refers to the same box
gives a `BoxRef`, and a loop of references (a box that refers to itself,
or A to B and B to A) ends. A reference to a box that was removed or
created again raises `BrokenReferenceError`, and one whose class this
process has not defined raises `UnknownBoxClassError`. `follow` is
keyword-only.

```python
from sharedbox import SharedBox


class Point(SharedBox):
    x: int = 0
    y: int = 0


point = Point()
point.update(x=3, y=4)
print(point.snapshot())  # {'x': 3, 'y': 4}
point.close()
Point.unlink()
```

### Reference fields

A field annotated with a `SharedBox` subclass `X`, or with `X | None` (or
`Optional[X]`), refers to another box, which keeps its own segment, lock
and lifetime. The field stores the box's name, schema hash and create id.

```python
from sharedbox import SharedBox


class Motor(SharedBox, identity="motor/1"):
    position: int = 0


class Stage(SharedBox):
    target: int = 0
    motor: Motor | None = None


with Motor() as motor, Stage() as stage:
    stage.motor = motor
    stage.motor.position = 100
    print(motor.position)  # 100
    print(stage.snapshot(follow=True))  # {'target': 0, 'motor': {'position': 100}}
    stage.motor = None
Stage.unlink()
Motor.unlink()
```

- Defaults work as for any field. `motor: Motor` and `motor: Motor | None`
  without a default are required; `= None`, `field(default=box)` and
  `field(default_factory=...)` give defaults, and the ordering rule,
  `kw_only`, `KW_ONLY` and `init=False` apply as in a dataclass. A default
  box is checked like an assigned one when the class is defined; a
  factory's result is checked at each creation. `__post_init__` may assign
  reference fields before the box is published. The class keeps the handle
  given as `field(default=box)` for as long as the class exists, which on
  Windows keeps that box's segment in existence; once that handle is
  closed, each creation that uses the default raises `BoxClosedError`.
- `motor: Motor` is never empty: assigning `None` raises `TypeError`, and
  reading it returns a box. `motor: Motor | None` may hold `None`. The two
  give different schema hashes, so a class that declares one cannot attach
  a box created by a class that declares the other.
- `X` is either the class being defined or a class defined before it. Any
  other name raises `TypeError` when the class is defined. A class that
  refers to itself through a required field cannot be created without an
  existing box of it, as with a dataclass, so a chain is declared
  `next: "Node | None" = None` (unquoted under
  `from __future__ import annotations`, or on Python 3.14).
- Assigning stores which box it is, under the outer box's lock. A box of
  `X`, of any subclass of `X`, or of any class with `X`'s schema hash (the
  same identity and fields) is accepted; any other value raises
  `TypeError`, and a closed box raises `BoxClosedError`. `update()` and the
  constructor take reference values with the same checks. Type checkers
  accept a box of `X` or of a subclass; a box of another class with `X`'s
  schema hash needs a `cast`.
- Reading attaches the box with its own class, which may be a subclass of
  `X`, and keeps that handle: later reads return the same object while the
  field refers to the same box. The class is found by the stored schema
  hash among the `SharedBox` classes this process has defined, so the
  process that reads must import the module that defines the box's class;
  otherwise the read raises `UnknownBoxClassError`. Of several classes with
  one schema hash, the first one defined that is still alive is used.
- After another thread or process assigns another box, the next read
  attaches that one and closes the handle it kept. `close()` on the outer
  box closes the handles its reads attached. Closing a returned box
  yourself makes the next read attach it again.
- A read raises `BrokenReferenceError` when the box was removed, or removed
  and created again under the same name (also under another class), since
  it was assigned. A handle that already read the field keeps its own
  mapping of the box and goes on returning it; on Windows that mapping
  keeps the box, and its name, in existence, so other handles can still
  attach it.
- The outer box only points at the other box: closing or unlinking the
  outer box leaves it alone, and no write covers both boxes at once.
- A pickled outer box carries no reference of its own; the unpickled box
  reads the field from the segment like any other handle.

### `events`

A psygnal `SignalGroup` with one signal per field. Each signal is emitted as
`(new, old)` when any thread or process changes the field.
`box.events.<field>.connect(cb)` listens to one field and
`box.events.connect(cb)` to all of them.

```python
import time

from sharedbox import SharedBox


class Valve(SharedBox):
    open: bool = False


valve = Valve()
valve.events.open.connect(lambda new, old: print(old, "->", new))
Valve.attach().open = True
time.sleep(0.5)  # prints: False -> True
valve.close()
Valve.unlink()
```

Signals differ from those of a local evented dataclass:

- Callbacks run on the box's watcher thread. Connect with `thread="main"`
  and call `psygnal.emit_queued()` from the main thread to run them there
  instead.
- Closing the box delivers writes the watcher thread had not seen yet, so
  callbacks may run once on the thread that calls `close()`, or on the
  thread that garbage collects the box.
- If several writes happen between two checks by the watcher thread, only
  one emission happens, with the latest value, and `old` is the value from
  the previous emission.
- A write that leaves the value unchanged emits nothing.
- For the first emission of a field, `old` is the value the field held when
  `events` was first accessed.
- A callback that raises is logged to the `sharedbox` logger. Callbacks
  connected before it on the same signal have already run; those connected
  after it do not run for that emission. Other fields still emit.
- The watcher thread also serves this box's `watch()` iterators, so a slow
  callback delays them.
- A callback that refers to the box, such as a lambda that reads a field,
  keeps the box alive until `close()`. Close such a box explicitly or use it
  in a `with` block.
- A field named like an attribute of psygnal's `SignalGroup` (`connect`,
  `disconnect`, `all`, `signals`, `block` and others) makes psygnal warn when
  the class is defined, and `box.events.<name>` then returns that attribute.
  `box.events["<name>"]` returns the field's signal.
- A child process created with `fork` while a callback is running can hang
  the first time that signal emits in the child, because the child inherits
  the signal's lock as held. Start child processes with `spawn` or
  `forkserver`, or fork while no callback runs.

`events` is an ordinary psygnal `SignalGroup`, so psygnal's own tools for
controlling emissions apply to it:

- `psygnal.qt.start_emitting_from_queue()` starts a Qt timer on the calling
  thread that runs callbacks connected with `thread="main"`, so a Qt
  application does not call `emit_queued()` itself.
- `blocked()` (or `block()` and `unblock()`) on a signal drops its emissions
  in this process while it is blocked; changes made meanwhile are not
  emitted later.
- `paused()` holds emissions made inside a block and releases them at the
  end, in order; with a reducer they are combined into one emission.
- `psygnal.throttled` and `psygnal.debounced` limit how often a callback
  runs, for fields that change many times a second.

```python
import time

from psygnal import throttled

from sharedbox import SharedBox


class Pump(SharedBox):
    flow: float = 0.0


@throttled(timeout=100)
def show_flow(new: float, old: float) -> None:
    print("flow", new)


pump = Pump()
pump.events.flow.connect(show_flow)
for i in range(20):
    pump.flow = float(i)
    time.sleep(0.01)
time.sleep(0.3)  # prints a few values, not twenty
pump.close()
Pump.unlink()
```

A reference field's signal is emitted when the field is assigned another
box or emptied, with `BoxRef` or `None` as `new` and `old`. Changes inside
the box it refers to are emitted by that box's own `events`, for example
`stage.motor.events.position`.

### `watch`

`watch(field)` returns a `FieldWatch` over the values written to `field`
from now on. See [`FieldWatch`](#fieldwatch). An unknown field raises
`ValueError`. A reference field yields `BoxRef` or `None`.

### `close` and the context manager

`close()` detaches this box from the segment and stops its watcher thread.
Other boxes on the same segment keep working, and the data stays. Reading
or writing a closed box raises `BoxClosedError`; `closed` tells whether
`close()` was called. Calling `close()` again does nothing. A box that is
garbage collected is closed.

A box is a context manager: leaving the `with` block calls `close()`.

```python
from sharedbox import SharedBox


class Lamp(SharedBox):
    on: bool = False


with Lamp() as lamp:
    lamp.on = True
print(lamp.closed)  # True
Lamp.unlink()
```

### `unlink`

`MyBox.unlink(name=None)` removes a segment's name, by default the class's.
`box.unlink()` removes the name of that box's segment. Boxes already
attached keep working. On Linux, later `attach()` calls then fail; on
Windows `unlink()` does nothing. Call it once, usually from the process
that created the box. See [Platform notes](#platform-notes)
for how this differs between Linux and Windows.

```python
from sharedbox import SharedBox


class Job(SharedBox):
    done: bool = False


with Job():
    pass
Job.unlink()
```

### `force_unlock`

`force_unlock()` releases a write lock left taken by a process that died
while writing. Reads and writes waiting on such a lock raise
`LockTimeoutError`, naming the process that holds it.

### Pickling

A pickled box holds its class, segment name, schema hash and the box's
create id, a random number drawn when the box was created. Unpickling
attaches a new handle: an independent box on the same data, which the
receiving process closes. `copy.copy` and `copy.deepcopy` do the same, like
`multiprocessing.shared_memory.SharedMemory`.

Unpickling fails with `SchemaMismatchError` when the receiving process's
class has different fields, or when the box under that name was unlinked
and created again since the pickle was made ("was pickled from a different
box named ..."). It fails with `SegmentNotFoundError` when the segment is
gone. Pickled boxes are for handing a box to a running process, not for
storing.

With the `fork` start method, arguments are not pickled: the child uses the
parent's box object, which keeps working after the fork.

Each unpickle opens the segment again, so a pool should attach once per
worker instead of pickling the box for every task:

```python
import multiprocessing as mp

from sharedbox import SharedBox


class Status(SharedBox):
    last_item: int = -1


status: Status


def start_worker(name: str) -> None:
    global status
    status = Status.attach(name)


def work(item: int) -> int:
    status.last_item = item
    return item * 2


if __name__ == "__main__":
    with Status() as box, mp.Pool(4, initializer=start_worker, initargs=(box.name,)) as pool:
        print(sum(pool.map(work, range(100))))  # 9900
    Status.unlink()
```

### Stored data

Values are stored as fixed-size bytes: `struct` encoding for numbers, UTF-8
for text. Stored bytes are never unpickled or executed. The layout of the
segment is specified in
[design/segment-layout.md](design/segment-layout.md).

### Passing a box to other libraries

A library that supports sharedbox, such as a C++ extension, takes the box
object itself:

```python
with Frame() as frame:
    camera.run(frame)            # camera: an extension that supports sharedbox
```

The library gets its own handle on the segment, so closing or unlinking
the box does not affect it.

### `__sharedbox_box__` (for library authors)

```python
def __sharedbox_box__(self, max_version: tuple[int, int] | None = None,
                      **kwargs) -> CapsuleType: ...
```

A protocol for extensions that accept a box, in the style of the Arrow
PyCapsule Interface. Python code does not call it; a consuming library does.

- It returns a PyCapsule named `"sharedbox_box"` holding a pointer to an
  `sbx_handle` (declared in `sharedbox/sharedbox_c.h`). The handle has its
  own mapping of the segment, made from the box's OS handle, so it works
  after `unlink()`.
- `max_version` is the `(major, minor)` layout version the caller
  supports. A major other than the box's raises `BufferError`; `None`
  means the box's own version. Any other keyword raises
  `NotImplementedError`.
- A consumer takes the handle with `sharedbox::handle::from_capsule` (C++)
  or `sbx_import` (C), renames the capsule `"used_sharedbox_box"`, compares
  the box's schema hash with the one it expects, and destroys (C++) or
  releases (C) the handle itself. A capsule that is never taken releases
  the handle when it is garbage collected.
- `release` touches no Python objects and does not need the GIL, so it may
  run on any thread and after interpreter shutdown.
- On Windows the segment stays alive while a handle is held.
- `sharedbox.SupportsSharedBox` is a `typing.Protocol` with this method,
  for annotating functions that accept a box:

  ```python
  from sharedbox import SupportsSharedBox

  def run(frame: SupportsSharedBox) -> None: ...
  ```

- `sharedbox.get_include()` returns the folder holding
  `sharedbox/sharedbox.hpp`, `sharedbox/sharedbox_c.h` and
  `sharedbox/sharedbox_c.cpp`, to add to an extension's include path.

[library-authors.md](library-authors.md) shows how to build an extension
that accepts a box.

## `field` and `fields`

```python
def field(*, default=MISSING, default_factory=MISSING, init=True, repr=True,
          kw_only=MISSING, metadata=None, doc=None) -> Any: ...
def fields(class_or_box) -> tuple[Field, ...]: ...

@dataclass(frozen=True)
class Field:
    name: str
    type: Any
    default: Any
    default_factory: Any
    init: bool
    repr: bool
    kw_only: bool
    metadata: Mapping[Any, Any]
    doc: str | None
```

`MISSING` is `dataclasses.MISSING`. The options of `field()`, against
`dataclasses.field`:

| Parameter | Supported | Behaviour |
| --- | --- | --- |
| `default` | yes | the field's value at creation when none is given |
| `default_factory` | yes | called once per creation that is given no value for the field; the result is converted into the segment like any value, so the box never keeps the object the factory returned |
| `init` | yes | `init=False` leaves the field out of the constructor; it needs `default` or `default_factory`, else `TypeError` when the class is defined |
| `repr` | yes | `repr=False` leaves the field out of `repr()` |
| `kw_only` | yes | per field, overriding the `kw_only` class keyword and `KW_ONLY` for that field |
| `metadata` | yes | a read-only mapping kept in Python, never stored in the segment |
| `doc` | yes | a string kept in Python |
| `compare`, `hash` | no | `field()` has no such parameters. Two boxes are equal only if they are the same object: two handles to one box share one record, so comparing boxes by their values is not defined |

- `default` and `default_factory` together raise `ValueError`.
- A field with neither is required. The rule that a positional field
  without a default cannot follow one with a default counts a field
  with either as having a default. `init=False` fields do not take a
  position and are not counted.
- A default is checked against the field's type and capacity when the
  class is defined, a factory's result when the box is created; both
  raise what assigning the value raises.
- `field()` on a name that is not a field (a name starting with `_`, a
  `ClassVar`) raises `TypeError` when the class is defined, and so does
  `field()` without an annotation, as in `dataclasses`
  (`TypeError: Job: 'retries' is a field but has no type annotation`).
- A subclass that sets a plain class attribute, without an annotation,
  on the name of an inherited field raises `TypeError` when it is
  defined. `dataclasses` ignores such an attribute; a box cannot,
  because the attribute would hide the field. Declare the field again
  with an annotation, which gives it default options, as in
  `dataclasses`. An annotation without a value keeps only a plain
  inherited `default`: the other options go back to their defaults, and
  a field whose base gave it a `default_factory` becomes required. The
  same holds for an `InitVar` declared over an inherited field.
- Values are copied into the segment, and only the stored types exist:
  no lists, dicts or other objects. `Capacity` stays in `Annotated`,
  because it belongs to the stored type, not to the field's options.
- Whether a field is keyword-only is fixed by the class that declares
  it, through its `kw_only` class keyword, a `KW_ONLY` among its own
  annotations, or `field(kw_only=...)`. A subclass keeps each inherited
  field's setting: its own `kw_only=True` affects only its own fields,
  and the fields of a `kw_only` base stay keyword-only in a subclass
  without the keyword, as in `dataclasses`.

`fields(class_or_box)` returns one `Field` per field of a `SharedBox`
subclass or box, in declaration order, without `InitVar` ones; anything
else raises `TypeError`. `Field` is read-only; `type` is the evaluated
annotation, `kw_only` is always a `bool`, and `default` and
`default_factory` are `MISSING` when not given. It is how `metadata`
and `doc` are read. Two `Field` objects are equal only if they are the
same object, as with `dataclasses.Field`.

`inspect.signature()` of a subclass gives its constructor's parameters,
as for a dataclass:

```python
import inspect

from sharedbox import SharedBox, field


class Job(SharedBox):
    attempts: int = field(default=0, init=False)
    retries: int = field(default_factory=lambda: 3)


print(inspect.signature(Job))  # (retries: int = <factory>) -> None
with Job() as job:
    print(job)  # Job(attempts=0, retries=3)
Job.unlink()
```

## `Capacity`

```python
@dataclass(frozen=True)
class Capacity:
    size: int
```

The most bytes a `str` or `bytes` field may hold, from 1 to 1048576
(1 MiB). It counts bytes, not characters: a UTF-8 character can take up to
4 bytes. Out-of-range sizes raise `ValueError`.

```python
from typing import Annotated

from sharedbox import Capacity, SharedBox


class Tag(SharedBox):
    text: Annotated[str, Capacity(4)] = ""


with Tag() as tag:
    tag.text = "abcd"
    try:
        tag.text = "été!"  # 6 bytes in UTF-8
    except ValueError as error:
        print(error)  # Tag.text holds at most 4 bytes; the value encodes to 6
Tag.unlink()
```

## `FieldWatch`

```python
class FieldWatch(Generic[T]):
    def __iter__(self) -> Iterator[T]: ...
    def __aiter__(self) -> AsyncIterator[T]: ...
```

Returned by `box.watch(field)`. Iterating with `for` blocks until the field
is written and yields the new value; `async for` waits the same way without
blocking the event loop. A consumer slower than the writers gets the latest
value and skips the ones in between. Iteration ends when the box is closed.

```python
import asyncio

from sharedbox import SharedBox


class Sensor(SharedBox):
    reading: float = 0.0


async def main() -> None:
    sensor = Sensor()

    async def write() -> None:
        writer = Sensor.attach()
        for value in (1.0, 2.0, 3.0):
            await asyncio.sleep(0.2)
            writer.reading = value
        writer.close()

    task = asyncio.create_task(write())
    async for reading in sensor.watch("reading"):
        print(reading)  # 1.0, then 2.0, then 3.0
        if reading == 3.0:
            break
    await task
    sensor.close()
    Sensor.unlink()


asyncio.run(main())
```

## `BoxRef`

```python
@dataclass(frozen=True)
class BoxRef:
    name: str
    schema_hash: int
    create_id: int
    box_class: type[SharedBox] | None   # read-only property
```

Which box a reference field refers to, as `snapshot()`, `events` and
`watch()` report it. `name` is the box's name, `schema_hash` that of the
box's own class, and `create_id` the random number drawn when the box was
created, which tells it from a box created later under the same name.
`box_class` is the class of this process with that schema hash, or `None`
when no such class is defined here.

## Errors

| Class | Base | Raised when |
| --- | --- | --- |
| `SegmentExistsError` | `FileExistsError` | creating a box under a name that is already taken; the message says whether the creator still runs (see [Creating and attaching](#creating-and-attaching)) |
| `SegmentNotFoundError` | `FileNotFoundError` | attaching to, or unlinking on Linux, a name with no segment; attaching to shared memory that does not become a box within 1 s |
| `SchemaMismatchError` | `TypeError` | attaching, or unpickling a box, with a class whose identity or fields differ from the creator's; a segment of another layout major version (the message names it); a segment with a field of a kind this version cannot read (the message names the kind); unpickling after the box was created again |
| `BoxClosedError` | `ValueError` | using a box after `close()` |
| `LockTimeoutError` | `TimeoutError` | a read or write waits for a write in progress for longer than `lock_timeout` |
| `BrokenReferenceError` | `LookupError` | reading a reference field, or `snapshot(follow=True)`, when the box it refers to was removed or created again since it was assigned; the message names the field, the box and which of the two happened |
| `UnknownBoxClassError` | `TypeError` | reading a reference field, or `snapshot(follow=True)`, when no class this process has defined has the stored box's schema hash; the message names the field, the box and the hash, and says to import the module that defines the class |

## Platform notes

Windows: the segment is a file mapping named `Local\sharedbox.<name>`,
backed by the page file. Windows frees it when the last box or capsule
handle using it is closed, and `unlink()` does nothing. A `lock_timeout` can
expire late by up to one timer tick, 15.6 ms at the default Windows timer
resolution.

Linux: the segment is the file `/dev/shm/sharedbox.<name>`, created with
mode `0600`, so only the same user can open it. Only `unlink()` removes its
name. A segment that is never unlinked stays until reboot, and creating a
box under its name raises `SegmentExistsError`. Every open box keeps one
file descriptor, so a process reaches the default limit of 1024
(`ulimit -n`) at about 1000 open boxes.

Releases up to 0.3.0rc0 named segments differently and used another
layout, so they and this release do not see each other's boxes.

macOS is not supported.
