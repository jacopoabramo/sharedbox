---
icon: lucide/wrench
---

# How to request work from the process that owns a box

Some state belongs to one process. The process that drives a camera knows
its temperature, and it decides which exposure the camera uses, so other
processes should read that state and ask for changes, not write it
themselves. An [attribute](../explanation/glossary.md#attribute) gives you
both: its owner publishes a value that every process reads, and other
processes send it requests that the owner acts on and answers. This guide
declares attributes, publishes them, serves their requests and handles what
can go wrong.

## 1. Declare an attribute

Annotate the [field](../explanation/glossary.md#field) `Attr[T]`, with `T`
the type of its value, or `Setting[T]` when other processes may ask to
change the value, and declare it with [`attr()`][sharedbox.attr]. An
attribute that takes arguments lists their types after `T`, and one that
only carries out a command has a `T` of `None`:

```{.python}
--8<-- "docs/examples/request_work_from_the_owner.py:declare"
```

Every attribute with a value stores the time of each publish of the
value. A [`Setting`][sharedbox.Setting] also holds a setpoint of the same
type as the value, which other processes ask to change with
[`set`][sharedbox.Setting.set]. An attribute starts at the zero of `T`, or
at `default`, which also gives a `Setting` its starting setpoint. A `T`
with no zero, such as a record without defaults, needs a `default`.

The box's constructor has no parameter for an attribute, because only its
owner writes it. Reading `camera.exposure` gives the
[`Attr`][sharedbox.Attr] bound to the box, the same object every time, and
assigning to it or passing it to `update` raises `TypeError`. `T` and the
argument types may be any type a record member may be, except a
[`Timed`][sharedbox.Timed], another `Attr` or a reference to a box.

An attribute takes requests when it has argument types, when its `T` is
`None`, or when it is a `Setting`. In the `Camera` above, `move` and `home`
take requests through a call, `exposure` through `set`, and `temperature`
takes none. A `Setting`'s request carries the new value as its one
argument, so a `Setting` takes no argument types and its `T` cannot be
`None`.

## 2. Publish its value

The owner writes an attribute with [`publish`][sharedbox.Attr.publish],
which takes the parts by keyword and writes all of them in one write, so a
reader never sees a value next to a setpoint from a different publish. Any
process reads the parts with `value`, `timestamp_ns` and, on a `Setting`,
`setpoint`, or all of them from one write with
[`read`][sharedbox.Attr.read]:

```{.python}
--8<-- "docs/examples/request_work_from_the_owner.py:publish"
```

A value published without `timestamp_ns` is stamped with the time of the
write. Give `timestamp_ns` when the device reports its own time. A
publish of the setpoint alone keeps the value and its time.

[`watch`][sharedbox.SharedBox.watch] and
[`events`][sharedbox.SharedBox.events] report publishes with the
[`AttrValue`][sharedbox.AttrValue] that `read` returns, as they report
writes of any field: several publishes in quick succession may reach you
as one change, with the parts of the last.
[`snapshot`][sharedbox.SharedBox.snapshot] includes the `AttrValue` too. A
request is not an event, so it reaches neither. Nothing checks which
process publishes, so keep `publish` in the owner's code.

## 3. Serve it

The process that owns the device registers a handler with
[`serve`][sharedbox.Attr.serve]. A `Setting`'s handler receives the value
given to `set`:

```{.python}
--8<-- "docs/examples/request_work_from_the_owner.py:serve"
```

Answering a request publishes nothing, so the handler publishes what other
processes should see. `set_exposure` publishes the setpoint it accepted and
then the value, and a handler for a real camera would configure it between
the two, so readers see the request accepted before the camera uses it.

A plain function runs on a thread of its own, so a slow handler doesn't
hold up what `watch` and `events` deliver. An `async def` handler runs as a
task on the event loop that is running when you call `serve`, and calling
`serve` with one outside a running loop raises `RuntimeError`. Only one
process serves a field at a time: a second `serve` while a live process
serves it, this one included, raises
[`AlreadyServedError`][sharedbox.AlreadyServedError].

[`RequestServer.stop`][sharedbox.RequestServer.stop] stops serving and
frees the field for another server. It waits for a running plain handler
to finish and answer first, and it asks a running `async def` handler to
cancel, whose caller then gets [`RequestError`][sharedbox.RequestError].
Closing the box stops every server it started, and so does garbage
collection of the box or the end of the process, but these free the field
at once: a request whose handler is still running fails with
`RequestError` saying the server stopped, and only then does the close
wait for a plain handler's thread.

## 4. Send a request

Calling the attribute, or `set` on a `Setting`, sends the request at
once and returns a [`RequestFuture`][sharedbox.RequestFuture], which you
wait on with `result` in any thread or `await` in a running event loop:

```{.python}
--8<-- "docs/examples/request_work_from_the_owner.py:request"
```

The request is sent whether or not anyone waits for the future, and
cancelling the future only stops the wait. A request takes positional
arguments only, so give a time limit to `result(timeout=...)`, or to
`asyncio.wait_for` in async code. A request sent while nobody serves the
field waits until a server starts.

## 5. Handle a failed request

An exception the handler raises is logged to the `sharedbox` logger in the
serving process, with its traceback, and reaches the caller as
[`RequestError`][sharedbox.RequestError], with the exception's type and
message cut to 256 bytes:

```{.python}
--8<-- "docs/examples/request_work_from_the_owner.py:error"
```

A caller waiting on a server whose process ends gets
[`ServerExitedError`][sharedbox.ServerExitedError] within about a tenth of
a second. A server that stops while it handles a request answers that
request, as section 3 describes, so no caller waits for an answer that
can't come.

## 6. Expect the latest request to win

The handler handles one request at a time. Of the requests sent while it
runs, only the newest is handled next, and each one before it raises
[`RequestSupersededError`][sharedbox.RequestSupersededError] in its caller.
The same holds while no server runs: the field keeps one request, so a
request that a later one replaces raises that error at once, and only the
newest waits for the server to start.
A camera told to move to 2 and then to 3 while it is still moving goes to
3:

```{.python}
--8<-- "docs/examples/request_work_from_the_owner.py:supersede"
```

If every request has to run, wait for each answer before sending the next.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/request_work_from_the_owner.py"
    ```
