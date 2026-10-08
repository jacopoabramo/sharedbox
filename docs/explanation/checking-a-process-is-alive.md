---
icon: lucide/lightbulb
---

# Checking a process is alive

Sometimes `sharedbox` has to answer a simple-sounding question: is the
process recorded in a [segment](glossary.md#segment) still running? The
answer decides whether a crashed waiter's slot can be freed, and what an
error message tells you about who holds a name. This page explains how it
answers, and why the obvious check isn't enough.

## A pid and a start time

The obvious check would be "is there a process with this process id
(pid)?", but that isn't enough: once a process exits, the operating system
can give its pid to a new one. So `sharedbox.hpp` names a process by its
pid and its start time, the way the `psutil` library tells a process from a
later one with the same pid.[^psutil] On Windows the start
time is the creation time from `GetProcessTimes`. On Linux it is field 22
of `/proc/<pid>/stat`.[^proc-stat] `process_alive()` reads the start time
of whatever process has the pid now and decides like this:

```d2 title="Is the recorded process still running?"
...@diagrams/style
direction: down
pid: "a process has the pid?" {class: step}
zombie: "on Linux: a zombie?" {
  class: step
  tooltip: A zombie, state Z or X in /proc/PID/stat, has exited and keeps its entry only until its parent collects it.
}
readable: "start time readable?" {
  class: step
  tooltip: It is not when the process belongs to another user or /proc is mounted with hidepid.
}
same: "same start time?" {class: step}
dead: "dead" {class: failed}
alive: "alive" {class: current}
pid -> dead: "no"
pid -> zombie: "yes"
zombie -> dead: "yes"
zombie -> readable: "no"
readable -> alive: "no" {
  tooltip: The process exists and nothing shows it is a different one, so it counts as alive.
}
readable -> same: "yes"
same -> alive: "yes"
same -> dead: "no, the pid was reused"
```

A recorded start time that couldn't be read is treated the same way: the
process counts as alive.[^proc-stat]

On Linux there's one more catch: containers give their processes their own
set of pids, called a pid namespace, so the same pid can mean different
processes in different containers. That's why
[waiter slots](glossary.md#waiter-slot) and the header's creator fields also
record the namespace, and a process in another namespace, or one whose
namespace is unknown, is never judged dead.

## Where the check is used

- Freeing the waiter slots of dead processes, as [Waiting for
  changes](waiting-for-changes.md#waking-a-process) describes.
- Naming the creator in
  [`SegmentExistsError`][sharedbox.SegmentExistsError]. When a create finds
  the name taken, the extension reads the existing header with
  `sharedbox::inspect()` and checks the creator it records. The creator
  writes the header before it publishes the [box](glossary.md#box), so a
  name that holds no published box is checked the same way. The docstring
  of `SegmentExistsError` lists the messages that result.

Even when the creator has exited, nothing is removed automatically, because
other processes may still be using a segment whose creator died. Removing
it is your call; [How to clean up segments](../how-to/clean-up-segments.md)
shows how.

## Sources

[^psutil]:
    psutil (BSD-3-Clause), `Process`: a process is identified by its pid
    and its creation time, so a reused pid is not mistaken for the same
    process.
    <https://github.com/giampaolo/psutil>

[^proc-stat]:
    Linux manual page `proc_pid_stat(5)`: the process state (field 3) and
    `starttime` (field 22).
    <https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html>
