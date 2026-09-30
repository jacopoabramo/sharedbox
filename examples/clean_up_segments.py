"""The script of the guide "How to clean up segments"."""

# --8<-- [start:close]
from sharedbox import LockTimeoutError, SharedBox


class Job(SharedBox, name="example-job"):
    done: bool = False


job = Job()
job.events.done.connect(lambda new, old: print(job.name, "done:", new))
job.done = True
job.close()
# --8<-- [end:close]

# --8<-- [start:unlink]
Job.unlink()
# --8<-- [end:unlink]

creator = Job(True)

# --8<-- [start:force-unlock]
job = Job.attach()
try:
    job.done = False
except LockTimeoutError as error:
    print(error)  # ... locked by pid 1234 ...
    # only once process 1234 is no longer running
    job.force_unlock()
    print(job.done)  # True: the write that timed out did not happen
    job.done = False
print(job.done)  # False
# --8<-- [end:force-unlock]

job.close()
creator.close()
Job.unlink()
