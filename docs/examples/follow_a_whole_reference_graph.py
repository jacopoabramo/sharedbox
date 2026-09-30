"""The script of the guide "How to follow a whole reference graph"."""

# --8<-- [start:classes]
import queue
from typing import Any

from sharedbox import SharedBox


class Encoder(SharedBox, name="example-encoder"):
    count: int = 0


class Motor(SharedBox, name="example-motor"):
    position: int = 0
    encoder: Encoder | None = None


class Stage(SharedBox, name="example-stage"):
    motor: Motor | None = None


# --8<-- [end:classes]

with Encoder() as encoder, Motor(0, encoder) as motor, Stage(motor) as stage:
    # --8<-- [start:follow-all]
    changes: queue.Queue[tuple[tuple[str, ...], Any]] = queue.Queue()
    stage.events.follow()
    stage.events.nested.connect(lambda path, new, old: changes.put((path, new)))
    encoder.count = 3
    print(changes.get(timeout=5))  # (('motor', 'encoder', 'count'), 3)
    motor.position = 7
    print(changes.get(timeout=5))  # (('motor', 'position'), 7)
    # --8<-- [end:follow-all]

    # --8<-- [start:follow-one-path]
    stage.events.unfollow()
    counts: queue.Queue[int] = queue.Queue()
    encoder_events = stage.events.follow("motor").follow("encoder")
    encoder_events.count.connect(lambda new, old: counts.put(new))
    encoder.count = 4
    print(counts.get(timeout=5))  # 4
    # --8<-- [end:follow-one-path]
Stage.unlink()
Motor.unlink()
Encoder.unlink()
