# API reference

Everything below is importable from `sharedbox`.

## `SharedBox`

```python
class SharedBox:
    def __init_subclass__(cls, *, name: str | None = None, kw_only: bool = False,
                          lock_timeout: float | None = None) -> None: ...
    def __init__(self, *args, **kwargs) -> None: ...
    @classmethod
    def create(cls, name: str, /, *args, **kwargs) -> Self: ...
    @classmethod
    def attach(cls, name: str | None = None) -> Self: ...
    name: str                    # read-only
    closed: bool                 # read-only
    events: SignalGroup          # read-only
    def update(self, **values) -> None: ...
    def snapshot(self) -> dict[str, Any]: ...
    def watch(self, field: str) -> FieldWatch: ...
    def force_unlock(self) -> None: ...
    def close(self) -> None: ...
    unlink                       # MyBox.unlink(name=None) or box.unlink()
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
```

A field is a public annotation with one of these types:

| Annotation | Stored as |
| --- | --- |
| `bool` | 1 byte |
| `int` | signed 64-bit integer; a larger value raises `OverflowError` |
| `float` | 64-bit float; an `int` is accepted and converted |
| `Annotated[str, Capacity(n)]` | UTF-8, at most `n` bytes |
| `Annotated[bytes, Capacity(n)]` | at most `n` bytes; `bytearray` and `memoryview` are accepted |

Names starting with `_` and `ClassVar` annotations are not fields. Any other
annotation raises `TypeError` when the class is defined, as does a class
with no fields or more than 256. Fields of a base class come first.

A field named like a `SharedBox` member (`name`, `closed`, `close`,
`unlink`, `update`, `snapshot`, `watch`, `events`, `force_unlock`, `create`,
`attach`) raises `TypeError` when the class is defined.

Fields are positional by default, in declaration order, as in a dataclass.
Fields after a `dataclasses.KW_ONLY` annotation are keyword-only. A default
is checked when the class is defined, and a positional field without a
default cannot follow one with a default.

Assigning a value of the wrong type raises `TypeError`, and a `str` or
`bytes` value longer than its capacity raises `ValueError`. Either way the
stored value does not change. Assigning to a name that is not a field raises
`AttributeError`.

Two boxes compare equal only if they are the same object.

### Class keywords

```python
from sharedbox import SharedBox


class Settings(SharedBox, name="app-settings", kw_only=True, lock_timeout=1.0):
    rate: float
    retries: int = 3


settings = Settings(rate=20.0)
print(settings.name, settings.retries)  # app-settings 3
settings.close()
```

- `name`: the segment name for boxes made by calling the class. By default
  it is `sharedbox-` followed by 16 hex digits derived from the class's
  module and qualified name, so every process that imports the class uses
  the same name. A name matches `[A-Za-z0-9_.-]{1,128}`; any other raises
  `ValueError`.
- `kw_only`: make every field keyword-only.
- `lock_timeout`: seconds to wait for the write lock before
  `LockTimeoutError`, 5.0 by default. Must be positive.

### Creating and attaching

Calling the class creates the segment under the class's name and writes the
given values. `create(name, ...)` does the same under another name, so one
class can describe several boxes. Both raise `SegmentExistsError` if the name is
taken.

`attach(name=None)` opens an existing segment, by default the one named
after the class. It raises `SegmentNotFoundError` if there is none, and
`SchemaMismatchError` if the segment was made by a different class or a
different version of this class.

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
```

`name` is the segment name, to pass to `attach()` in another process.

### `update` and `snapshot`

`update(**values)` writes several fields under one lock: a reader sees all
of the new values or none of them. It checks every value before it writes
anything. `snapshot()` returns every field's value, read at one point in
time.

```python
from sharedbox import SharedBox


class Point(SharedBox):
    x: int = 0
    y: int = 0


point = Point()
point.update(x=3, y=4)
print(point.snapshot())  # {'x': 3, 'y': 4}
point.close()
```

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
```

Signals differ from those of a local evented dataclass:

- Callbacks run on the box's watcher thread. Connect with `thread="main"`
  and call `psygnal.emit_queued()` from the main thread to run them there
  instead.
- If several writes happen between two checks by the watcher thread, only
  one emission happens, with the latest value, and `old` is the value from
  the previous emission.
- A write that leaves the value unchanged emits nothing.
- For the first emission of a field, `old` is the value the field held when
  `events` was first accessed.
- A callback that raises is logged to the `sharedbox` logger. Callbacks
  connected before it on the same signal have already run; those connected
  after it do not run for that emission. Other fields still emit.

### `watch`

`watch(field)` returns a `FieldWatch` over the values written to `field`
from now on. See [`FieldWatch`](#fieldwatch). An unknown field raises
`ValueError`.

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
```

### `unlink`

`MyBox.unlink(name=None)` removes a segment's name, by default the class's.
`box.unlink()` removes the name of that box's segment. Boxes already
attached keep working; new `attach()` calls fail. Call it once, usually
from the process that created the box. See [Platform notes](#platform-notes)
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
while writing. Writers waiting on such a lock raise `LockTimeoutError`,
naming the process that holds it.

### Pickling

A pickled box holds only its class and segment name. Unpickling attaches to
the segment, so a box can be passed to a child process as an argument.

```python
import multiprocessing as mp

from sharedbox import SharedBox


class Progress(SharedBox):
    percent: float = 0.0


def work(progress: Progress) -> None:
    progress.percent = 100.0
    progress.close()


if __name__ == "__main__":
    with Progress() as progress:
        child = mp.Process(target=work, args=(progress,))
        child.start()
        child.join()
        print(progress.percent)  # 100.0
```

### Stored data

Values are stored as fixed-size bytes: `struct` encoding for numbers, UTF-8
for text. Stored bytes are never unpickled or executed.

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
        print(error)  # text holds at most 4 bytes; the value encodes to 6
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


asyncio.run(main())
```

## Errors

| Class | Base | Raised when |
| --- | --- | --- |
| `SegmentExistsError` | `FileExistsError` | creating a box under a name that is already taken |
| `SegmentNotFoundError` | `FileNotFoundError` | attaching to, or unlinking on Linux, a name with no segment |
| `SchemaMismatchError` | `TypeError` | attaching with a class whose module, name or fields differ from the creator's |
| `BoxClosedError` | `ValueError` | using a box after `close()` |
| `LockTimeoutError` | `TimeoutError` | the write lock stays taken for longer than `lock_timeout` |

## Platform notes

Windows: the segment is backed by the page file. Windows frees it when the
last box using it is closed, and `unlink()` does nothing.

Linux: the segment is a file in `/dev/shm`, created with mode `0600`, so only
the same user can open it. Only `unlink()` removes its name. A segment that
is never unlinked stays until reboot, and creating a box under its name
raises `SegmentExistsError`.

macOS is not supported.
