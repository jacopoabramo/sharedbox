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

# --8<-- [start:force-unlock]
job = Job()
try:
    job.done = False
except LockTimeoutError as error:
    print(error)
    # only once the process the message names is no longer running
    job.force_unlock()
# --8<-- [end:force-unlock]

job.close()
Job.unlink()
