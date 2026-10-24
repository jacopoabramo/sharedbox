"""The script the sharedbox tutorial builds, one page at a time."""

# --8<-- [start:imports]
import multiprocessing as mp
import queue
import time
from typing import Annotated

from sharedbox import Capacity, SharedBox

# --8<-- [end:imports]


# --8<-- [start:mailbox]
class Mailbox(SharedBox):
    letters: int = 0
    flag_up: bool = False
    owner: Annotated[str, Capacity(32)] = ""


# --8<-- [end:mailbox]


# --8<-- [start:deliver]
def deliver(letters: int) -> None:
    mailbox = Mailbox.attach("tutorial-mailbox")
    mailbox.letters = letters
    mailbox.close()


# --8<-- [end:deliver]


# --8<-- [start:share]
def share() -> None:
    with Mailbox.create("tutorial-mailbox", owner="Ada") as mailbox:
        carrier = mp.Process(target=deliver, args=(2,))
        carrier.start()
        carrier.join()
        print("letters left by the carrier:", mailbox.letters)
    Mailbox.unlink("tutorial-mailbox")


# --8<-- [end:share]


# --8<-- [start:react]
def react() -> None:
    with Mailbox.create("tutorial-mailbox", owner="Ada") as mailbox:
        mailbox.events.letters.connect(
            lambda new, old: print(f"letters {old} -> {new}")
        )
        letters = mailbox.watch("letters")
        carrier = mp.Process(target=deliver, args=(3,))
        carrier.start()
        watched = next(iter(letters))
        carrier.join()
    print("watched", watched)
    Mailbox.unlink("tutorial-mailbox")


# --8<-- [end:react]


# --8<-- [start:carrier]
class Carrier(SharedBox):
    stop: Mailbox | None = None


# --8<-- [end:carrier]


# --8<-- [start:follow]
def follow() -> None:
    with (
        Mailbox.create("tutorial-north") as north,
        Mailbox.create("tutorial-south") as south,
        Carrier.create("tutorial-carrier", north) as carrier,
    ):
        seen: queue.Queue[int] = queue.Queue()
        carrier.events.follow("stop").letters.connect(lambda new, old: seen.put(new))
        north.letters = 1
        print("letters at the stop", seen.get(timeout=5))
        carrier.stop = south
        # forwarding moves to the new box on a background thread, shortly after
        time.sleep(0.5)
        south.letters = 7
        print("letters at the stop", seen.get(timeout=5))
    Carrier.unlink("tutorial-carrier")
    Mailbox.unlink("tutorial-north")
    Mailbox.unlink("tutorial-south")


# --8<-- [end:follow]


# --8<-- [start:main]
if __name__ == "__main__":
    share()
    react()
    follow()
# --8<-- [end:main]
