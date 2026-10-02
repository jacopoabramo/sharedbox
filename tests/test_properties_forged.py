import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import settings


@pytest.mark.parametrize("script", ["forged_open.py", "forged_types.py"])
def test_forged_bytes_never_crash_an_open(script: str) -> None:
    """Check that opening segments with forged headers, tables or records never ends the process with a crash."""
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name(script)),
            str(settings().max_examples),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    # A crash reading out of bounds ends the child with a signal or an access violation.
    assert result.returncode == 0, result.stdout + result.stderr
