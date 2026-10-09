---
icon: lucide/wrench
---

# How to send items through a stream

A [stream](../explanation/glossary.md#stream) carries a series of items from
one process to others, such as the batches of a data pipeline, and keeps the
last few in shared memory so that a reader that falls behind can catch up.
Use it when the items are a flow: a [box](../explanation/glossary.md#box)
holds one current value that every process reads and writes, while a stream
holds items that each reader receives once.

## Create the stream

The item type can be anything a box field takes, except a reference to a box.
This guide sends a record with an index and an array:

```{.python}
--8<-- "docs/examples/send_items_through_a_stream.py:types"
```

The creating process calls [`create`][sharedbox.SharedStream.create] with the
item type, a name and a `capacity`, the number of items the ring holds:

```python
stream = SharedStream.create(Batch, "example-pipeline:batches", capacity=8)
```

A larger capacity lets a reader fall further behind before a lossless
reader holds the sender back or a lossy one misses items, and costs memory
for that many items. Other processes call
[`attach`][sharedbox.SharedStream.attach] with the same type and name.
`max_readers` (8 unless you set it) limits how many readers may be open at
once. A stream name follows the same rules as [a box name](name-a-box.md).

## Send items

A stream has one [sender](../explanation/glossary.md#sender). You get it from
[`sender()`][sharedbox.SharedStream.sender] and send with
[`send`][sharedbox.StreamSender.send]. Closing the sender ends the stream,
and `with` closes it at the end of the block:

```{.python}
--8<-- "docs/examples/send_items_through_a_stream.py:main"
```

A call to `sender()` while another live sender exists raises
[`StreamBusyError`][sharedbox.StreamBusyError]. Once a sender has closed the
stream, a later `sender()` raises [`EndOfStream`][sharedbox.EndOfStream].

## Receive items

A [reader](../explanation/glossary.md#reader) is opened with
[`reader`][sharedbox.SharedStream.reader], and its `mode` decides what
happens when it is slower than the sender:

- `"lossless"` receives every item, and the sender waits for it when it
  falls `capacity` items behind. Use it for a consumer that must see
  everything, such as a writer that saves each item.
- `"lossy"` skips the items the sender has already overwritten, and
  [`missed`][sharedbox.StreamReader.missed] counts them. Use it for a viewer
  or a GUI that can drop items.
- `"latest"` always receives the newest item. Use it for a display that
  shows only the current state.

A reader starts at the newest item already sent, or, with
`start="oldest"`, at the oldest the stream still holds. Reading with `for`
receives until the stream ends:

```python
for batch in stream.reader(mode="lossy"):
    print(batch.index)
```

!!! warning "A reader opened late misses earlier items"
    A reader starts at the newest item already sent, so of the items sent
    before it opens, the reader receives only the last. Open the reader
    first and tell the sender it can start, as the `ready` event does below,
    or use `start="oldest"` while the stream still holds what you need.

## Read into arrays you already have

[`receive`][sharedbox.StreamReader.receive] builds new arrays for every item.
Between processes an item is always copied, so reading into the same arrays
each time saves allocating a new one per item. Allocate them once and pass
them to [`receive_into`][sharedbox.StreamReader.receive_into], or to
[`iter_into`][sharedbox.StreamReader.iter_into] for a `for` loop:

```{.python}
--8<-- "docs/examples/send_items_through_a_stream.py:reader"
```

The reader sets `ready` once it is open, and the main process waits for it
before sending, so no item is sent before the reader exists.

`out` has the shape of the item. For an item that is an array, `out` is the
array. For a record, it is a mapping from member name to an array, to `None`
for a member decoded as usual, or, for a record or tuple member, to an `out`
of its own. For a tuple, it is a tuple with one entry per member. The
returned item holds your arrays. A member that is a list, set or dict takes
`None`, because an array cannot be an element of a collection, and an item
that is itself a collection has no arrays to read into.

## Run a callback for each item

[`reader.events`][sharedbox.StreamReader.events] is a
[`ReaderEvents`][sharedbox.ReaderEvents] group with two `psygnal` signals:
`received`, emitted with `(item, position)` for each item, and `ended`, once
when the stream ends:

```python
reader = stream.reader()
reader.events.received.connect(lambda item, position: print(item.index))
reader.events.ended.connect(lambda: print("done"))
```

The first `received` callback you connect starts the reader's background
thread, and disconnecting the last one stops it. The callbacks run on that
thread. To run them on your main thread, connect with `thread="main"` and
call `psygnal.emit_queued()` in your loop.

A reader that delivers to callbacks cannot also be read from, because one
reader cannot hand an item to two consumers. Any receive on it raises
`RuntimeError` until the callbacks disconnect, so open a second reader for
the other consumer.

!!! warning "A slow callback loses items or holds the sender"
    Callbacks run one after another on the reader's thread. While one runs,
    a lossy or latest reader misses items, and a lossless reader holds the
    sender back. Do the slow part elsewhere, for example by putting the item
    on a queue.

Delivery starts on the first connect because `sharedbox` relies on `psygnal`
calling `SignalInstance._append_slot` and `_remove_slot` and on its `_lock`,
three private names that the test suite checks against the installed
`psygnal`.

## Wait, poll or give up

[`receive`][sharedbox.StreamReader.receive] and
[`send`][sharedbox.StreamSender.send] take a `timeout` in seconds and raise
`TimeoutError` when it passes. The `_nowait` forms,
[`receive_nowait`][sharedbox.StreamReader.receive_nowait],
[`receive_into_nowait`][sharedbox.StreamReader.receive_into_nowait] and
[`send_nowait`][sharedbox.StreamSender.send_nowait], raise
[`WouldBlock`][sharedbox.WouldBlock] when there is nothing to receive or no
room to send. A `KeyboardInterrupt` that lands while a receive is returning
may lose the item it was taking, as `queue.Queue.get` can.

## Use a stream with asyncio

A reader works with `async for` and `anext`, and a sender has
[`asend`][sharedbox.StreamSender.asend]. Neither blocks the event loop:

```python
async for batch in reader:
    print(batch.index)

await sender.asend(batch)
```

To give up after a while, put `asyncio.timeout()` around the receive:

```python
async with asyncio.timeout(5):
    batch = await anext(reader)
```

!!! warning "A cancelled receive keeps its item for the next one"
    When a task is cancelled after the reader had already taken an item,
    the reader keeps that item and returns it from the next receive, so a
    lossless reader loses nothing. If two tasks share the reader, that item
    can come after a later one, and it holds the arrays of the call that took
    it, not the `out` of the next. Let one task or thread use a reader at a
    time.

A cancelled `asend` may still have sent its item, if room in the stream
appeared just as it was cancelled, so sending it again can send it twice.

Both ends can be used with `async with`, which closes them at the end of the
block. In an `asyncio.TaskGroup`, let the task that sends close the sender,
because a reader's `async for` ends only when the stream does:

```{.python}
--8<-- "docs/examples/send_items_through_a_stream.py:tasks"
```

The reader is opened before either task starts, so it sees every item.

To wait for whichever of several readers has an item first,
[`receive_future`][sharedbox.StreamReader.receive_future] returns a
`concurrent.futures.Future`, which `asyncio.wrap_future` turns into one the
event loop can wait on. A future that has started receiving cannot be
cancelled, and an item it takes after you stop waiting on it is lost. Keep
one future per reader, and ask a reader for a new one only once its last
one is done:

```{.python}
--8<-- "docs/examples/send_items_through_a_stream.py:first"
```

## Close the stream and handle exits

Closing the sender ends the stream: readers receive what is buffered, then
get [`EndOfStream`][sharedbox.EndOfStream]. A `for` loop treats that as its
end, and a direct `receive` raises it. Closing a reader, or the stream with
[`close`][sharedbox.SharedStream.close], raises
[`StreamClosedError`][sharedbox.StreamClosedError] in a call still waiting on
it. On Linux, `SharedStream.unlink(name)` removes the name.

When a process dies, the others notice it during a wait. Readers end after a
dead sender, and the sender stops waiting for a dead lossless reader. A
reader that only polls with `receive_nowait` never waits, so it keeps raising
`WouldBlock` and never sees the stream end; give it a `timeout` instead.

`sharedbox` registers an `atexit` hook in each process that stops its box
watcher threads and its stream worker threads. `atexit` runs handlers
last-registered first, so a handler you register before importing `sharedbox`
runs after `sharedbox`'s and finds the ends closed. Spawned child processes
exit through `sys.exit` and run the hook. Children made by `fork` or
`forkserver` exit with `os._exit` and do not, so their daemon threads end with
the process, and the other processes free a reader ended that way after a
short delay.

A child made by `fork` inherits the parent's ends, but they are closed in the
child: using one raises [`StreamClosedError`][sharedbox.StreamClosedError].
Open new ends in the child instead. Do not connect callbacks to an inherited
reader's events either: if the parent was delivering to callbacks when it
forked, the child can block on the lock the parent's delivery thread held.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/send_items_through_a_stream.py"
    ```
