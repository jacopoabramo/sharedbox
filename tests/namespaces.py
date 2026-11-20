"""The namespace value sharedbox records beside a pid, for tests that write it into a segment themselves."""

import os
import sys

# PROC_TIME_INIT_INO in the Linux kernel.
INITIAL_TIME_NAMESPACE = 0xEFFFFFFA


def own_pidns() -> int:
    """Return this process's namespace value as sharedbox.hpp records it, 0 on Windows."""
    return 0 if sys.platform == "win32" else linux_namespace_value()


def linux_namespace_value() -> int:
    """Return the pid namespace's inode, with the time namespace's in the upper 32 bits unless it is the initial one."""
    pid_namespace = os.stat("/proc/self/ns/pid").st_ino
    try:
        time_namespace = os.stat("/proc/self/ns/time").st_ino
    except FileNotFoundError:
        return pid_namespace
    if time_namespace == INITIAL_TIME_NAMESPACE:
        return pid_namespace
    return pid_namespace | time_namespace << 32
