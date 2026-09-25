import contextlib
import os
import sys
import uuid
from collections.abc import Iterator

import pytest


@pytest.fixture
def unique_name() -> Iterator[str]:
    name = f"sbtest-{uuid.uuid4().hex[:16]}"
    yield name
    if sys.platform.startswith("linux"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f"/dev/shm/{name}")
