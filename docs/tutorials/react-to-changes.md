---
icon: lucide/play
---

# Reacting to changes

In [Sharing a record between processes](share-a-record.md), the first
process only learned how many letters had arrived after the second process had ended.
In this tutorial you make it notice the change the moment it happens, in two
ways: with a callback, and with a loop that waits for each new value.

## Before you start

!!! note "What you need"

    The script `mailbox.py` from
    [Sharing a record between processes](share-a-record.md).

## 1. Listen for a change

Add this function below `share`:

```{.python}
--8<-- "docs/tutorials/mailbox.py:react"
```

It listens in two ways at once. The first is
[`events`][sharedbox.SharedBox.events], which has one signal per
[field](../explanation/glossary.md#field). The signals come from `psygnal`,
a small library for callbacks: the lambda you connect to `events.letters`
runs each time any process changes `letters`, and gets the new and the old
value. The second is
[`watch`][sharedbox.SharedBox.watch], which gives you an iterator over the
values written to `letters` from now on, so `next` waits for the first
one.

Both rely on the [box](../explanation/glossary.md#box)'s [watcher](../explanation/glossary.md#watcher), a background
thread that waits for writes from any process. While it waits, it holds
one of the box's [waiter slots](../explanation/glossary.md#waiter-slot);
[Waiting for changes](../explanation/waiting-for-changes.md) explains how a
write wakes it up.

## 2. Run it

In the `if` block at the end of the file, add the highlighted line, so the
script runs the new function after the old one:

```{.python hl_lines="3"}
if __name__ == "__main__":
    share()
    react()
```

Run the script:

```bash
uv run mailbox.py
```

You should see the first tutorial's line, then the two new ones:

```text
letters left by the carrier: 2
letters 0 -> 3
watched 3
```

The callback runs on the watcher thread, while `next` returns on your main
thread, so the two could print at the same moment and mix their lines. That
is why the script prints the watched value only after the `with` block:
closing the box stops the watcher thread and waits for it to finish, and
since the callback runs on that thread, it has printed by then.

!!! warning "Callbacks run on another thread"
    A callback runs on the watcher thread, not on your main thread. If your
    program must handle changes on its main thread, for example because it
    has a window, connect the callback with `thread="main"`. `psygnal` then
    queues each call, and your main thread runs the queued calls whenever it
    calls `psygnal.emit_queued()`.

## 3. Wait in asyncio code

If your program uses `asyncio`, `watch` works with `async for` too, and
waits without blocking the event loop. Try it in a separate script,
`scale.py`, about the scale that weighs parcels at the counter:

```python
import asyncio

from sharedbox import SharedBox


class Scale(SharedBox):
    weight: float = 0.0


async def main() -> None:
    scale = Scale()

    async def weigh() -> None:
        writer = Scale.attach()
        for value in (1.0, 2.0, 3.0):
            await asyncio.sleep(0.2)
            writer.weight = value
        writer.close()

    task = asyncio.create_task(weigh())
    async for weight in scale.watch("weight"):
        print(weight)  # 1.0, then 2.0, then 3.0
        if weight == 3.0:
            break
    await task
    scale.close()
    Scale.unlink()


asyncio.run(main())
```

Run it with `uv run scale.py`, and it prints each value as the writer
stores it:

```text
1.0
2.0
3.0
```

`Scale()` creates the box under a name taken from the class, and
`Scale.attach()` opens it by that same name. To keep the example short, the
writer is a task in the same process, but a writer in another process wakes
the loop in exactly the same way.

## What you built

You have a box that tells you about each write from another process as it
happens, both through a callback and through a loop you can also use in
`asyncio` code.

## Next steps

- [Referring to another box](refer-to-another-box.md) is next: a carrier
  that points at a mailbox, and a callback that follows it to whichever
  mailbox it points at.
- [Waiting for changes](../explanation/waiting-for-changes.md) explains the
  watcher and the rules for when a callback runs.
- [`FieldWatch`][sharedbox.FieldWatch] and
  [`BoxEvents`][sharedbox.BoxEvents] list what the iterator and the signals
  do.
