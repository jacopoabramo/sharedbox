---
icon: lucide/wrench
---

# How to send items through a stream

A [stream](../explanation/glossary.md#stream) carries a series of items from
one process to others, such as the letters a sorting office passes on, and
keeps the last few in shared memory so that a reader that falls behind can
catch up.
Use it when the items are a flow: a [box](../explanation/glossary.md#box)
holds one current value that every process reads and writes, while a stream
holds items that each reader receives once.

## Create the stream

The item type can be anything a box field takes, except a reference to a box.
This guide sends letters, each a record with an index, a recipient and the
scan of its envelope as an array:

```{.python}
--8<-- "docs/examples/send_items_through_a_stream.py:types"
```

The scan takes its sizes from the `Shape` in its annotation. Because
`Letter` is a dataclass, the sizes could go in the field's metadata instead,
as in `scan: npt.NDArray[np.uint8] = field(metadata={"shape": (32, 32)})`,
which works for an `attrs` class too. A `NamedTuple` or a `msgspec`
`Struct` has no field metadata and keeps the `Shape`.

The creating process calls [`create`][sharedbox.SharedStream.create] with the
item type, a name and a `capacity`, the number of items the ring holds:

```python
stream = SharedStream.create(Letter, "example-sorting:letters", capacity=8)
```

A larger capacity lets a reader fall further behind before a lossless
reader holds the sender back or a lossy one misses items, and costs memory
for that many items. `max_readers` (8 unless you set it) limits how many
readers may be open at once. A stream name follows the same rules as
[a box name](name-a-box.md).

Other processes reach the stream in one of two ways. A process you start can
take the stream as an argument, and it arrives attached under the same name,
as in the example below. Any other process calls
[`attach`][sharedbox.SharedStream.attach] with the same type and name. When
only processes you start use the stream, leave the name out and `create`
gives the stream a new one:

```python
stream = SharedStream.create(Letter, capacity=8)
```

The sender and the readers cannot be passed to another process: pickling
one raises `TypeError`, and a child made by `fork` inherits them closed.
Pass the stream, and open each end in the process
that uses it. Code that is given only an end reaches its stream through
[`stream`][sharedbox.SharedReceiveStream.stream], to open another reader
or read its [`statistics`][sharedbox.SharedStream.statistics].

## Send items

A stream has one [sender](../explanation/glossary.md#sender). You get it from
[`sender()`][sharedbox.SharedStream.sender] and send with
[`send`][sharedbox.SharedSendStream.send]. Closing the sender ends the stream,
and `with` closes it at the end of the block:

```{.python}
--8<-- "docs/examples/send_items_through_a_stream.py:main"
```

A call to `sender()` while another live sender exists raises
[`StreamBusyError`][sharedbox.StreamBusyError]. Once a sender has closed the
stream, a later `sender()` raises [`EndOfStream`][sharedbox.EndOfStream].

## Fill an item in place

When your code can write an item's arrays itself, such as a device library
that fills a buffer it is given, it can write them straight into the stream
and leave `send` no array bytes to copy. Each item lives in a slot of the
stream's ring. [`writing`][sharedbox.SharedSendStream.writing] reserves
the next slot and yields a [`SendSlot`][sharedbox.SendSlot], whose
[`item`][sharedbox.SendSlot.item] is the template item you pass with each
of its arrays replaced by an array over the slot. The template's arrays are
never read, so an empty array will do. Fill the slot's arrays, then send the
item with [`slot.send`][sharedbox.SendSlot.send], which finds those arrays
already in place and does not copy them:

```{.python}
--8<-- "docs/examples/send_items_through_a_stream.py:writing"
```

Readers see nothing of the slot until it is sent. Leaving the block without
`slot.send`, or by an exception, publishes nothing and gives the position to
the next send, so the block can also drop an item it finds bad. While the
slot is reserved, a send from another thread or task waits for it. In
asyncio code, use
[`awriting`][sharedbox.SharedSendStream.awriting] with `async with`.

!!! warning "Write the item's arrays only inside the block"
    The arrays stay usable after the block, but they still view the slot.
    Writing to them after `slot.send` changes an item readers may already
    have received, and nothing tells them. A child process started by `fork`
    inherits the arrays as well, and writing to them there changes the
    parent's slot.

## Receive items

A [reader](../explanation/glossary.md#reader) is opened with
[`reader`][sharedbox.SharedStream.reader], and its `mode` decides what
happens when it is slower than the sender:

- `"lossless"` receives every item, and the sender waits for it when it
  falls `capacity` items behind. Use it for a consumer that must see
  everything, such as the registered-mail desk, which logs every letter.
- `"lossy"` skips the items the sender has already overwritten, and
  [`missed`][sharedbox.SharedReceiveStream.missed] counts them. Use it for
  a viewer that can drop items, such as a display board in the hall.
- `"latest"` always receives the newest item. Use it for a display that
  shows only the current state.

A reader starts at the newest item already sent, or, with
`start="oldest"`, at the oldest the stream still holds. Reading with `for`
receives until the stream ends:

```python
for letter in stream.reader(mode="lossy"):
    print(letter.index)
```

!!! warning "A reader opened late misses earlier items"
    A reader starts at the newest item already sent, so of the items sent
    before it opens, the reader receives only the last. Open the reader
    first and tell the sender it can start, as the `ready` event does below,
    or use `start="oldest"` while the stream still holds what you need.

## Read into arrays you already have

[`receive`][sharedbox.SharedReceiveStream.receive] builds new arrays for
every item. Between processes an item is always copied, so reading into
the same arrays each time saves allocating a new one per item. Allocate
them once and pass them to
[`receive_into`][sharedbox.SharedReceiveStream.receive_into], or to
[`iter_into`][sharedbox.SharedReceiveStream.iter_into] for a `for` loop:

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

## Send arrays whose shape changes

An array declared with a `Shape` has the same shape in every item. When the
size changes from one item to the next, such as the weights of the parcels
in a sack, declare the array with a [`MaxShape`][sharedbox.MaxShape]
instead. Each item then stores its own shape, which may run from 0 up to the
maximum along each dimension. Every slot keeps room for the maximum, so
choose the smallest maximum your data allows:

```{.python}
--8<-- "docs/examples/send_items_through_a_stream.py:shapes"
```

`send` takes an array of any shape that fits the maximum. With `writing`,
the template's array gives the shape of the array over the slot, and its
values are still never read, so `np.empty` will do. When you know the count
only after filling the slot, send the first `n` rows of the slot's array, as
`weights[:5]` does above. That array already starts at its place in the
slot, so `slot.send` writes only its shape and copies nothing.

`receive` returns an array of the shape stored with the item.
`receive_into` takes an array of the maximum shape and puts into the item a
view of that array in the stored shape, over its first elements in C order.
For an array of more than one dimension, those elements are the first rows
only when every dimension but the first is at its maximum, so read the
values through the item rather than by slicing your own array.

A `MaxShape` array belongs to a stream item, as the item itself or inside
its records and tuples. A box field, and a member of an optional, a union or
a collection, cannot hold one.

## Run a callback for each item

[`reader.events`][sharedbox.SharedReceiveStream.events] is a
[`ReceiveEvents`][sharedbox.ReceiveEvents] group with two `psygnal` signals:
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

[`receive`][sharedbox.SharedReceiveStream.receive] and
[`send`][sharedbox.SharedSendStream.send] take a `timeout` in seconds and raise
`TimeoutError` when it passes. The `_nowait` forms,
[`receive_nowait`][sharedbox.SharedReceiveStream.receive_nowait],
[`receive_into_nowait`][sharedbox.SharedReceiveStream.receive_into_nowait] and
[`send_nowait`][sharedbox.SharedSendStream.send_nowait], raise
[`WouldBlock`][sharedbox.WouldBlock] when there is nothing to receive or no
room to send. A `KeyboardInterrupt` that lands while a receive is returning
may lose the item it was taking, as `queue.Queue.get` can.

## Release a reader that holds the sender back

A lossless reader that falls a full ring behind holds the sender back, so a
sender that cannot wait, such as a sorting machine that never stops, loses
the item for every reader when its send times out. Open that sender with
[`on_full="release"`][sharedbox.SharedStream.sender] instead. Once a send
has waited its whole `timeout`, it releases the lossless readers that hold it
back and sends the item:

```{.python}
--8<-- "docs/examples/send_items_through_a_stream.py:release"
```

A released reader is told on its next receive, which raises
[`ReaderReleased`][sharedbox.ReaderReleased]. Its `missed` attribute counts
the items the reader lost, and the reader goes on at the newest item, so
the next receive works. A `for` loop over the reader ends on the exception,
which is why `file_letters` catches it and loops again. With a ring of four
slots and six letters, the reader above prints `missed 5`, then files letter
5. A reader that delivers to callbacks emits
[`events.released`][sharedbox.ReceiveEvents] with the count instead and
goes on delivering.

The release happens only when the `timeout` passes, so a send with no
`timeout` waits as before, and [`send_nowait`][sharedbox.SharedSendStream.send_nowait]
releases at once. [`asend`][sharedbox.SharedSendStream.asend] and
[`awriting`][sharedbox.SharedSendStream.awriting] take a `timeout` for the
same reason, because a wait that `asyncio.wait_for` cancels releases
nothing. A reader opened by sharedbox 0.7.0 or earlier cannot be released,
so a send it holds back still raises `TimeoutError`.

## Use a stream with asyncio

A reader works with `async for` and `anext`, and a sender has
[`asend`][sharedbox.SharedSendStream.asend]. Neither blocks the event loop:

```python
async for letter in reader:
    print(letter.index)

await sender.asend(letter)
```

To give up after a while, put `asyncio.timeout()` around the receive:

```python
async with asyncio.timeout(5):
    letter = await anext(reader)
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
[`receive_future`][sharedbox.SharedReceiveStream.receive_future] returns a
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
it. On Linux, `SharedStream.unlink(stream.name)` removes the name.

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
Open new ends in the child from the inherited stream instead. Do not connect callbacks to an inherited
reader's events either: if the parent was delivering to callbacks when it
forked, the child can block on the lock the parent's delivery thread held.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/send_items_through_a_stream.py"
    ```
