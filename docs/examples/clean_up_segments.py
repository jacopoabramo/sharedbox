"""The script of the guide "How to clean up segments"."""

# --8<-- [start:close]
from sharedbox import LockTimeoutError, SharedBox, segment_status


class Delivery(SharedBox, name="example-delivery"):
    done: bool = False


delivery = Delivery()
delivery.events.done.connect(lambda new, old: print(delivery.name, "done:", new))
delivery.done = True
delivery.close()
# --8<-- [end:close]

# --8<-- [start:unlink]
Delivery.unlink()
# --8<-- [end:unlink]

# --8<-- [start:status]
print(segment_status("example-delivery"))  # missing
# --8<-- [end:status]

creator = Delivery(True)

# --8<-- [start:force-unlock]
delivery = Delivery.attach()
try:
    delivery.done = False
except LockTimeoutError as error:
    print(error)  # ... locked by pid 1234 ...
    # only once process 1234 is no longer running
    delivery.force_unlock()
    print(delivery.done)  # True: the write that timed out did not happen
    delivery.done = False
print(delivery.done)  # False
# --8<-- [end:force-unlock]

delivery.close()
creator.close()
Delivery.unlink()
