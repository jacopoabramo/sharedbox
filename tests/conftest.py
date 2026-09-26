import contextlib
import os
import sys
import uuid
from collections.abc import Iterator

import pytest

# A spawned child inherits this from its parent's environment, so a fixed
# segment name built from it stays the same across a run's own processes
# while differing from any other run's.
TEST_RUN = os.environ.setdefault("SHAREDBOX_TEST_RUN", uuid.uuid4().hex[:8])


@pytest.fixture
def unique_name() -> Iterator[str]:
    name = f"sbtest-{uuid.uuid4().hex[:16]}"
    yield name
    if sys.platform.startswith("linux"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f"/dev/shm/{name}")
