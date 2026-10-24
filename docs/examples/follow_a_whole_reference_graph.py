"""The script of the guide "How to follow a whole reference graph"."""

# --8<-- [start:classes]
import queue
from typing import Any

from sharedbox import SharedBox


class Bag(SharedBox, name="example-bag"):
    letters: int = 0


class Carrier(SharedBox, name="example-carrier"):
    parcels: int = 0
    bag: Bag | None = None


class PostOffice(SharedBox, name="example-post-office"):
    carrier: Carrier | None = None


# --8<-- [end:classes]

with Bag() as bag, Carrier(0, bag) as carrier, PostOffice(carrier) as office:
    # --8<-- [start:follow-all]
    changes: queue.Queue[tuple[tuple[str, ...], Any]] = queue.Queue()
    office.events.follow()
    office.events.nested.connect(lambda path, new, old: changes.put((path, new)))
    bag.letters = 3
    print(changes.get(timeout=5))  # (('carrier', 'bag', 'letters'), 3)
    carrier.parcels = 7
    print(changes.get(timeout=5))  # (('carrier', 'parcels'), 7)
    # --8<-- [end:follow-all]

    # --8<-- [start:follow-one-path]
    office.events.unfollow()
    counts: queue.Queue[int] = queue.Queue()
    bag_events = office.events.follow("carrier").follow("bag")
    bag_events.letters.connect(lambda new, old: counts.put(new))
    bag.letters = 4
    print(counts.get(timeout=5))  # 4
    # --8<-- [end:follow-one-path]
PostOffice.unlink()
Carrier.unlink()
Bag.unlink()
