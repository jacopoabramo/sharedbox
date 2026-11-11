---
icon: lucide/wrench
---

# How to send bluesky documents to another process

A `bluesky` `RunEngine` reports each run as a series of
[documents](../explanation/glossary.md#document), and the code that reads
them, such as a live plot or a data reduction, often belongs in another
process so it cannot slow the run down. `sharedbox.bluebox` carries the
documents there through a [channel](../explanation/glossary.md#channel):
the `RunEngine` sends to it, and each receiving process gets the same
documents in the same order, with the arrays of each event copied through
shared memory instead of being pickled.

`sharedbox.bluebox` needs `numpy`, which the `bluebox` extra installs:

```bash
pip install "sharedbox[bluebox]"
```

It runs on CPython 3.11 to 3.14 with the GIL. It imports nothing from
`bluesky`, `ophyd-async` or `event-model`, so a receiving process needs
none of them.

## Send the documents of a run

[`DocumentSender.create`][sharedbox.bluebox.DocumentSender.create] makes
the channel under a name of your choice, which follows the rules of
[a box name](name-a-box.md). The sender is a callback with the signature
the `RunEngine` expects, so you subscribe it directly. This guide scans a
simulated stage with a simulated detector from `ophyd_async.sim`:

```{.python}
--8<-- "docs/examples/send_bluesky_documents_to_another_process.py:devices"
```

```{.python}
--8<-- "docs/examples/send_bluesky_documents_to_another_process.py:sender"
```

Closing the sender, here at the end of the `with` block, ends the channel:
each receiver delivers the documents it still holds and then stops. A
second sender on the same name raises `SegmentExistsError` while the first
one's process runs. Once that process has exited, `create` removes what it
left behind and takes the name.

!!! warning "On Windows an open receiver keeps the old channel"
    Windows frees a segment only when every process has let go of it, so
    while a receiver of the old channel is still open, even one that has
    seen the end but not been closed or collected, `create` on the same
    name raises `SegmentExistsError`. Close receivers when their loop ends,
    as a `with` block does.

## Receive them in another process

[`DocumentReceiver.attach`][sharedbox.bluebox.DocumentReceiver.attach]
opens the channel by its name, and iterating over the receiver yields each
document's name and the document, as the `RunEngine` passed them to the
sender:

```{.python}
--8<-- "docs/examples/send_bluesky_documents_to_another_process.py:receiver"
```

The loop ends when the sender closes. The script prints:

```text
start
descriptor
event 1 ['detector-channel-1-value', 'detector-channel-2-value', 'detector-channel-3-value', 'stage-x']
event 2 ['detector-channel-1-value', 'detector-channel-2-value', 'detector-channel-3-value', 'stage-x']
event 3 ['detector-channel-1-value', 'detector-channel-2-value', 'detector-channel-3-value', 'stage-x']
stop
```

The receiver sets `ready` once it is attached, and the main process waits
for it before the scan starts, so the receiver sees the whole run. The
channel must exist before a receiver attaches: `attach` on a name no sender
has created raises `SegmentNotFoundError`. A receiver that attaches while
no run is under way waits for the next start.
[`receive`][sharedbox.bluebox.DocumentReceiver.receive] returns one
document at a time and takes a `timeout`, for a loop that has other work to
do between documents.

A receiver gets every kind of document the run emits, `resource`, `datum`,
`stream_resource` and `stream_datum` included. Dict keys keep the order they
were sent in, except an event's `data` and `timestamps`, whose keys follow
the descriptor's `data_keys`. Some values come back in another type that compares equal
or holds the same numbers:

- an enum member arrives as its value, and a `pydantic` model, such as the
  `Table` an `ophyd-async` device can put in its configuration, as the dict
  of its fields;
- a tuple arrives as a list;
- in an event, a value arrives in the type its descriptor states: an int in
  a `number` key as a float, a list in an `array` key as a `numpy` array,
  a numpy scalar as a Python one, and a float in a `float32` key rounded to
  that precision;
- an event's `time` and timestamps arrive as floats; in an event page,
  each numeric column of `data` arrives as a `numpy` array, and `time`,
  `seq_num` and timestamps as lists;
- an event or page without `filled` gets `filled: {}`, and fields of an
  event other than `uid`, `time`, `seq_num`, `data`, `timestamps`,
  `filled` and `descriptor` are dropped.

## Choose what a slow receiver loses

A receiver that reads more slowly than the run sends events can hold the run
back, or skip events, depending on the `mode` you attach it with:

- `"lossless"`, the default, receives every event, and the sender waits for
  it when it falls a full ring of events behind. Use it for code that must
  see everything, such as a writer that saves the run.
- `"lossy"` skips the events the sender has already written over. Use it
  for a live plot, which can drop points.
- `"latest"` always receives the newest event. Use it for a display of the
  current value.

```python
receiver = DocumentReceiver.attach("beamline", mode="lossy")
```

The mode applies only to events and event pages. Every receiver gets every
other document, the start, descriptors and stop included, whatever its
mode, so a lossy receiver still knows which run and which stream an event
belongs to. A receiver that reads those documents more slowly than the run
sends them therefore holds the run back for up to `control_timeout` and is
then released, whatever its mode. The events it
does get arrive in the order they were sent.

How many events the ring holds is `event_capacity`, 8 unless you pass it to
`create`, and `max_readers`, also 8, limits how many receivers may be
attached at once. A lossy or latest receiver takes two of those places,
because it holds a second, lossy reader on the channel's control stream,
which tells the sender not to wait for its events.

Each event stream holds `event_capacity` items, and each item has room for
an event page of `page_rows` rows, 16 unless you set it, so every array key
takes `event_capacity` times `page_rows` times its own size in shared
memory. A 1024 by 1024 `uint16` image takes 2 MiB per event, which comes to
256 MiB for that stream with the defaults. When your events are large and
your run sends single events rather than pages, pass `page_rows=1`; a page
with more rows than `page_rows` is split over several items and still
arrives whole.

## Attach while a run is under way

A receiver can attach at any time. When a run is under way, it first
delivers that run's start and descriptors, then the documents sent from the
moment it attached, so it can make sense of the events it receives without
having seen the beginning. Of the events sent before it attached, it gets
those sent after the last document of another kind, as far as their stream
still holds them; the others are gone.

The channel keeps the run's start and descriptors for late receivers, in at
most 1 MiB.
A run whose start and descriptors are larger is still sent, but a receiver
that attaches during it raises
[`LateJoinError`][sharedbox.bluebox.LateJoinError]; attach again after the
run's stop.

## Decide how long the run waits

A lossless receiver can make the run wait, so the sender gives you two ways
to limit that.

Every document other than an event or event page goes through the
channel's control stream, which every receiver reads without loss. A receiver that stays
alive but stops reading it holds the sender back for at most
`control_timeout` seconds, 5 unless you set it. The sender then releases
that receiver and goes on. A released receiver has lost documents it cannot
get back, so its next `receive`, or its `for` loop, raises
[`ReceiverReleasedError`][sharedbox.bluebox.ReceiverReleasedError] and it
closes. Attach a new receiver to go on from the run under way.

Events have no such limit by default. When a lossless receiver falls a full
ring of events behind, the next event waits for it as long as it takes, so
a receiver that stops reading in the middle of a scan stalls the run until
it reads again, and loses nothing. It is released only if a document other
than an event, such as the stop, waits `control_timeout` for it. Set `event_timeout` to limit that wait. With
`on_full="wait"`, the default, an event that waits longer than
`event_timeout` raises `TimeoutError` in the `RunEngine`. With
`on_full="release"`, the sender releases the receiver instead, and the run
goes on:

```python
sender = DocumentSender.create(
    "beamline", event_timeout=1.0, on_full="release"
)
```

!!! warning "A lossless receiver that reaches a descriptor late can be released"
    A receiver starts reading a descriptor's events when a call to
    `receive` reads the descriptor, which can be while it is still
    delivering earlier documents. Each call reads the control documents
    waiting, up to as many as the control stream's ring holds. Before a
    descriptor's events fill their ring, the sender waits for each lossless
    receiver to read the descriptor, and gives up on it once
    `control_timeout` passes without it reading a control document,
    whatever `on_full` says. Giving up applies to every receiver of that
    process for the rest of the run. A lossless receiver that reads the
    descriptor after that finds events gone, and its next `receive` raises
    `ReceiverReleasedError` instead of delivering a run with holes. Give
    the sender a `control_timeout` longer than the time a lossless
    receiver spends away from `receive`.

## Chain channels into a pipeline

A process can receive from one channel and send on its own, changing the
documents or adding new ones on the way, such as a reduction that adds a
stream of sums for a viewer further down:

```python
receiver = DocumentReceiver.attach("beamline", mode="lossless")
with DocumentSender.create("beamline:reduced") as sender:
    for name, doc in receiver:
        sender(name, doc)
        if name == "event":
            for out_name, out_doc in reduce(doc):
                sender(out_name, out_doc)
```

An `event_model.DocumentRouter` is a convenient way to write `reduce`.

When every receiver in a pipeline is lossless, the slowest stage sets the
pace of the run, because each stage waits in its own send and stops reading
the channel before it. With the default `on_full="wait"` and no
`event_timeout`, that wait has no limit, so the run loses nothing and goes
as slowly as the last stage. To keep a slow last stage from holding the run
back, let the stage in front of it release the last stage or skip its
events: give the middle stage's sender `on_full="release"` and an `event_timeout`, so it releases
the last stage, or attach the last stage with `mode="lossy"`.

## What the sender refuses

Events travel as typed records whose layout comes from their descriptor's
`data_keys`, so the sender checks each descriptor when it arrives. It raises
`ValueError` naming the key, and sends nothing, when a key's shape has a
dimension of variable size (`None` or `-1`), or a number's `dtype_numpy` has
a byte order other than the machine's. Convert such data before it reaches
the sender.

An event raises `ValueError` naming the key, and nothing of it is sent, when
an array's shape or dtype differs from its descriptor, a value does not
convert to its key's type, or a string is longer than `string_capacity`
bytes of UTF-8, 256 unless you set it. An array key with no `dtype_numpy`
has room for numbers only, so an event whose array under such a key holds
strings raises `ValueError` when they do not fit; give the key a
`dtype_numpy`. A document holding a value the channel cannot encode, such
as an arbitrary object, raises `TypeError`.

## Close the channel and handle exits

Closing the sender ends the channel and removes its names. A receiver sees
the end after delivering every document it holds: a `for` loop stops, and a
direct `receive` raises [`EndOfStream`][sharedbox.EndOfStream].

When the sender's process dies, receivers end the same way, after the
documents they already hold. When a receiver's process dies, the sender
stops waiting for it. On Linux, a channel whose sender exited without
closing it, killed or not, leaves its segments in `/dev/shm` until the next
`DocumentSender.create` on the same name removes them, so close the sender
or use it in a `with` block.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/send_bluesky_documents_to_another_process.py"
    ```
