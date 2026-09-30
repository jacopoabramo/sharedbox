"""The script the sharedbox tutorial builds, one page at a time."""

# --8<-- [start:imports]
import multiprocessing as mp
import queue
import time
from typing import Annotated

from sharedbox import Capacity, SharedBox

# --8<-- [end:imports]


# --8<-- [start:motor]
class Motor(SharedBox):
    position: int = 0
    enabled: bool = False
    label: Annotated[str, Capacity(32)] = ""


# --8<-- [end:motor]


# --8<-- [start:move]
def move(position: int) -> None:
    motor = Motor.attach("tutorial-motor")
    motor.position = position
    motor.close()


# --8<-- [end:move]


# --8<-- [start:share]
def share() -> None:
    with Motor.create("tutorial-motor", label="x-axis") as motor:
        other = mp.Process(target=move, args=(10,))
        other.start()
        other.join()
        print("position set by the other process:", motor.position)
    Motor.unlink("tutorial-motor")


# --8<-- [end:share]


# --8<-- [start:react]
def react() -> None:
    with Motor.create("tutorial-motor", label="x-axis") as motor:
        motor.events.position.connect(
            lambda new, old: print(f"position {old} -> {new}")
        )
        positions = motor.watch("position")
        other = mp.Process(target=move, args=(20,))
        other.start()
        for position in positions:
            print("watched", position)
            break
        other.join()
    Motor.unlink("tutorial-motor")


# --8<-- [end:react]


# --8<-- [start:stage]
class Stage(SharedBox):
    motor: Motor | None = None


# --8<-- [end:stage]


# --8<-- [start:follow]
def follow() -> None:
    with (
        Motor.create("tutorial-x") as x,
        Motor.create("tutorial-y") as y,
        Stage.create("tutorial-stage", x) as stage,
    ):
        seen: queue.Queue[int] = queue.Queue()
        stage.events.follow("motor").position.connect(
            lambda new, old: seen.put(new)
        )
        x.position = 1
        print("motor position", seen.get(timeout=5))
        stage.motor = y
        # forwarding moves to the new box on a background thread, shortly after
        time.sleep(0.5)
        y.position = 7
        print("motor position", seen.get(timeout=5))
    Stage.unlink("tutorial-stage")
    Motor.unlink("tutorial-x")
    Motor.unlink("tutorial-y")


# --8<-- [end:follow]


# --8<-- [start:main]
if __name__ == "__main__":
    share()
    react()
    follow()
# --8<-- [end:main]
