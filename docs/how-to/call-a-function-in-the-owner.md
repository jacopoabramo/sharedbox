---
icon: lucide/wrench
---

# How to call a function in the process that owns a box

Some requests mean more than writing a value, such as moving a motor to a
position. The process that drives the device has to act
on them and say how it went. A [field](../explanation/glossary.md#field) of
type [`Call`][sharedbox.Call] carries such a request from any process to the
one process that serves the field, and carries the answer back. This guide
declares one, serves it, calls it and handles what can go wrong.

## 1. Declare a call field

Annotate the field `Call[[A, ...], R]`, with the argument types in the list
and the result type after it, or `None` when the call returns nothing:

```{.python}
--8<-- "docs/examples/call_a_function_in_the_owner.py:declare"
```

A call field holds no value, so it takes `field(init=False)` and the box's
constructor has no parameter for it. Without it, type checkers count the
field as a constructor parameter and flag `Scanner()` as missing an
argument. Reading `scanner.move` gives the call itself, and assigning to it,
`update`, `watch` and `events` refuse it with `TypeError`. The arguments and
the result may be any type a record member may be, except a
[`Timed`][sharedbox.Timed], another `Call` or a reference to a box.

## 2. Serve it

The process that owns the device registers a handler with
[`serve`][sharedbox.Call.serve]:

```{.python}
--8<-- "docs/examples/call_a_function_in_the_owner.py:serve"
```

A plain function runs on a thread of its own, so a slow handler doesn't
hold up what [`watch`][sharedbox.SharedBox.watch] and
[`events`][sharedbox.SharedBox.events] deliver. An
`async def` handler runs as a task on the event loop that is running when
you call `serve`, and calling `serve` with one outside a running loop
raises `RuntimeError`. Only one process serves a field at a time: a second
`serve` while a live process serves it, this one included, raises
[`CallServedError`][sharedbox.CallServedError].

[`CallServer.stop`][sharedbox.CallServer.stop] stops serving and frees the
field for another server. Closing the box stops every server it started,
and so does the end of the process.

## 3. Call it

Calling the field sends the request at once and returns a
[`CallFuture`][sharedbox.CallFuture], which you wait on with `result` in
any thread or `await` in a running event loop:

```{.python}
--8<-- "docs/examples/call_a_function_in_the_owner.py:call"
```

The request is sent whether or not anyone waits for the future, and
cancelling the future only stops the wait. A call takes positional
arguments only, so give a time limit to `result(timeout=...)`, or to
`asyncio.wait_for` in async code. A request sent while nobody serves the
field waits until a server starts.

## 4. Handle a failed call

An exception the handler raises is logged to the `sharedbox` logger in the
serving process, with its traceback, and reaches the caller as
[`CallError`][sharedbox.CallError], with the exception's type and message
cut to 256 bytes:

```{.python}
--8<-- "docs/examples/call_a_function_in_the_owner.py:error"
```

A caller waiting on a server whose process ends gets
[`ServerExitedError`][sharedbox.ServerExitedError] within about a tenth of
a second. Stopping a server waits for a running plain handler to finish and
answer, while a running `async def` handler is cancelled and its caller
gets `CallError`, so no caller waits for an answer that can't come.

## 5. Expect the latest request to win

The handler handles one request at a time. Of the requests sent while it
runs, only the newest is handled next, and each one before it raises
[`CallSupersededError`][sharedbox.CallSupersededError] in its caller. A
motor told to go to 2 and then to 3 while it is still moving goes to 3:

```{.python}
--8<-- "docs/examples/call_a_function_in_the_owner.py:supersede"
```

If every request has to run, wait for each answer before sending the next.

??? example "The whole script"

    ```{.python}
    --8<-- "docs/examples/call_a_function_in_the_owner.py"
    ```
