"""The OS object names of boxes, for tests that open or remove the shared memory themselves."""

PREFIX = "SBX:"


def os_name(name: str) -> str:
    """Return the shared memory name of the box called `name`, without the leading `/` on Linux."""
    return PREFIX + name
