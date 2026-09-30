---
icon: lucide/play
---

# Reacting to changes

In [Sharing a record between processes](share-a-record.md), the first
process read the new position after the second process had ended. In this
tutorial it sees the change as it happens, in two ways: a callback, and a
loop over the values written.

## Before you start

!!! note "What you need"

    The script `motor.py` from
    [Sharing a record between processes](share-a-record.md).

## 1. Listen for a change

Add this function below `share`:

```{.python}
--8<-- "docs/tutorials/motor.py:react"
```

[`events`][sharedbox.SharedBox.events] has one psygnal signal per
[field](../explanation/glossary.md#field).
The lambda connected to `events.position` runs each time any process
changes `position`, with the new and the old value.
[`watch`][sharedbox.SharedBox.watch] returns an iterator over the values
written to `position` from now on, and `next` waits for the first of them.

The [box](../explanation/glossary.md#box)'s
[watcher](../explanation/glossary.md#watcher) serves both: a
background thread that waits for writes from any process. While it
waits, it holds one of the box's
[waiter slots](../explanation/glossary.md#waiter-slot).
[Waiting for changes](../explanation/waiting-for-changes.md) explains how a
write wakes it.

## 2. Run it

Add the highlighted line at the end of the file:

```{.python hl_lines="3"}
if __name__ == "__main__":
    share()
    react()
```

Run the script:

```bash
uv run motor.py
```

```text
position set by the other process: 10
position 0 -> 20
watched 20
```

The callback runs on the watcher thread and `next` returns on the main
thread, so both could print at the same moment. The script prints the
watched value after the `with` block instead: closing the box waits for the
watcher thread, so the callback has printed by then.

A program that must handle a change on its main thread, such as one with a
window, connects the callback with `thread="main"`. psygnal then queues each
call, and the main thread runs the queued calls when it calls
`psygnal.emit_queued()`.

## 3. Wait in asyncio code

`watch` also works with `async for`, which waits without blocking the event
loop. This example is a separate script, `sensor.py`:

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

`Sensor()` creates the box under a name taken from the class, and
`Sensor.attach()` opens it by that name. The writer here is a task in the
same process; a writer in another process wakes the loop the same way.

## What you built

A box that reports each write from another process as it happens, to a
callback and to a loop.

## Next steps

- [Referring to another box](refer-to-another-box.md) is the next
  tutorial: a box that refers to a motor, and a callback that follows the
  reference.
- [Waiting for changes](../explanation/waiting-for-changes.md) explains the
  watcher and the rules for when a callback runs.
- [`FieldWatch`][sharedbox.FieldWatch] and
  [`BoxEvents`][sharedbox.BoxEvents] list what the iterator and the signals
  do.
