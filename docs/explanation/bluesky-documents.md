---
icon: lucide/lightbulb
---

# How bluebox carries documents

`sharedbox.bluebox` moves the [documents](glossary.md#document) of `bluesky`
runs between processes over [streams](glossary.md#stream), and the two kinds
of document it carries need different things. Events are many and
small, follow a layout their descriptor fixes in advance, and often hold
arrays, so they should cost no more than a copy of their values. Every other
document is rare but can hold anything a `dict` holds, at any size. This
page explains how a [channel](glossary.md#channel) serves both, how a
receiver puts them back in order, and what it costs when a receiver is slow.
[How to send bluesky documents to another process](../how-to/send-bluesky-documents-to-another-process.md)
shows how to use it.

## A control stream and one stream per descriptor

A channel named `N` is a few [segments](glossary.md#segment) whose names
start with `N`. The control stream, `N:control`, carries every document
other than events and event pages. Each descriptor gets a stream of its own,
`N:e<k>`, which carries that descriptor's events and nothing else; `k` is
the number the sender gave the descriptor, as
[the next section](#putting-the-documents-back-in-order) explains. A box, `N:state`, holds the
channel's settings and the run under way.

The split follows the documents. A descriptor lists its `data_keys` with
the shape and dtype of each, so before its first event arrives the sender
knows exactly what every event of that stream holds. It builds a record
type from the keys, one [field](glossary.md#field) per key, with arrays of
the stated shape and dtype, plus one array of timestamps and the event's
own `uid`, `seq_num` and `time`. The event stream's items are that record,
so an event is sent the way any stream item is: its numbers and arrays are
copied into the ring and nothing is encoded. A key that a typed field
cannot hold, such as a text array or a key with no dtype, is stored as
CBOR in a bytes field of its own, and only that key pays for encoding.

The other documents have no layout known in advance, so the control stream
carries them as CBOR in parts of 16 KiB. A start document's metadata or a
descriptor's configuration can be large, and splitting it lets the stream's
items stay a fixed size however large the documents are. Arrays inside them
are written as CBOR typed arrays, which keep their dtype and shape.

## Putting the documents back in order

The sender numbers every document it sends, whatever stream it goes to,
and writes that number into the item. A receiver reads the control stream
and every open event stream, and always delivers the item with the lowest
number among those it holds. Because the streams are separate, an event can
be waiting in its stream while a document sent before it, such as a second
descriptor or a `datum`, is still arriving in parts. The receiver holds the
event back
until the gap before it is filled, so the order you receive is the order the
`RunEngine` emitted.

A receiver can open an event stream only once it has read the stream's
descriptor, which gives the layout. At each `receive` it therefore reads the
control documents already sent, ahead of what it delivers and up to as many
as the control stream's ring holds, and opens the event stream of each new
descriptor as soon as it has read the descriptor. A receiver that holds a
full ring of documents reaches a new descriptor only after delivering some
of them, and a lossless receiver must not miss the events sent before that
moment. The sender therefore waits for every lossless receiver to open the
stream when a descriptor's events start to fill their ring. While a
receiver goes on reading control documents it is still on its way, so the
sender gives up on it only after `control_timeout` passes without it
reading one. Giving up applies to the receiver's process, which is not
waited for again in that run, so a stuck receiver costs a run that much
once, not once per descriptor, and the sender does not wait for a process
that has exited. A lossy
or latest receiver tells the sender not to wait for it, by holding a second
reader on the control stream that it never reads. That reader is lossy, so
it never holds the sender back and is never released.

## Modes apply to events only

Each receiver reads the control stream with a lossless
[reader](glossary.md#reader), whatever mode it chose, because a receiver
that missed a descriptor could not read any of its events, and one that
missed a start or stop would mix two runs. The mode you choose applies to
the event streams, where losing an event loses one set of readings and
nothing else. A lossy receiver that falls behind skips events, and one that
waits for a gap in the order gives up after 10 ms when only an event could
fill it, since that event may be gone for good.

A receiver that stops reading the control stream would hold every other
receiver back with it, so the sender releases it after `control_timeout`.
The released receiver has lost documents it cannot get back, and it reports
that with `ReceiverReleasedError` instead of going on with a run that has
holes. A receiver reads ahead at most a ring of control documents, so one
that delivers them more slowly than the run sends them fills the ring as
well, whatever its mode. The sender then holds back for up to
`control_timeout` and releases it, instead of letting it fall further
behind for the rest of the run.

## Joining a run under way

A receiver that attaches in the middle of a run has missed the start and
the descriptors, without which the events that follow mean nothing. The
sender keeps them in the box `N:state`, as CBOR, together with the names of
the event streams and the number of the last document that changed them.
The receiver reads the box twice. From the first read it opens the run's
event streams, at the oldest item each still holds, so from then on a
lossless receiver holds their sender back and the stop cannot remove them.
It then opens its control reader and reads the box again, so anything sent
after the second read is in the reader, and it drops the control items
whose number is at or below the box's. The start and descriptors it
delivers come from the second read; it opens the streams that read adds and
closes those it no longer lists.

The control reader starts at the last control item sent before it opened.
An event numbered at or below that item comes before a document the
receiver never delivers, and one at or below the box's number comes before
a descriptor it does deliver, so the receiver drops the events numbered at
or below the larger of the two. It keeps every later event, those sent while
it was attaching included, and every event it delivers comes after the
control documents it missed.

A field of a box holds at most 1 MiB, so a run whose start and descriptors
need more cannot be joined. The sender sets a flag in the box instead, and
a receiver that attaches during such a run raises `LateJoinError`.

## When the run waits

A lossless receiver slows the run to its own pace, as a lossless reader of
any stream does. That is the right default for a receiver that saves data,
but the run is usually the part that cannot wait, so the sender limits how
long each kind of document waits. A control document waits at most
`control_timeout` for a receiver that stopped reading. An event waits as
long as it takes unless you give `event_timeout`, and with
`on_full="release"` the sender then releases the receiver as it does on
the control stream.

A pipeline of channels adds up these waits. A middle stage that receives
from one channel and sends on another waits in its own send when the stage
after it is slow, and stops reading meanwhile, so the run goes at the pace
of the slowest lossless stage unless the sender in front of that stage is
told to release it.
