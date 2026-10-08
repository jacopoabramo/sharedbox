---
icon: lucide/lightbulb
---

# When to use sharedbox

Should you use a box, or would something simpler do? A
[box](glossary.md#box) is made for sharing a record between processes, and
that has a price: every read and write turns the value into bytes in shared
memory or back, which costs some tens to hundreds of nanoseconds, and every
read gives you a copy.

If all your code runs in threads of one process, you don't need to pay
that price: threads can share ordinary Python objects, which is several
times faster and works with any Python type. psygnal's `evented`
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
