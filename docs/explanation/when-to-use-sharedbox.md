---
icon: lucide/lightbulb
---

# When to use sharedbox

A [box](glossary.md#box) is for sharing a record between processes. Every
read and write encodes the value to or from bytes in shared memory, which
costs a few hundred nanoseconds and means a read returns a copy.

Threads of one process can share ordinary Python objects instead, which is
several times faster and keeps any Python type. psygnal's `evented`
dataclasses give such an object the same `events` interface as a box, and a
`threading.Lock` makes several changes appear at once, as
[`update`][sharedbox.SharedBox.update] does:

```python
import threading
from dataclasses import dataclass

from psygnal import evented


@evented
@dataclass
class Motor:
    position: int = 0
    enabled: bool = False


motor = Motor()
lock = threading.Lock()
motor.events.position.connect(lambda new, old: print(old, "->", new))
with lock:
    motor.position = 10              # prints: 0 -> 10
```

Unlike a box's signals, these callbacks run on the thread that changes the
field, before the assignment returns. A box's callbacks run on its
[watcher](glossary.md#watcher) thread; [Waiting for
changes](waiting-for-changes.md) explains why.
