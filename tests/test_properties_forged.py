import subprocess
import sys
from pathlib import Path

from hypothesis import settings


def test_forged_bytes_never_crash_an_open() -> None:
    """Check that opening segments with forged bytes never ends the process with a crash."""
    script = Path(__file__).with_name("forged_open.py")
    result = subprocess.run(
        [sys.executable, str(script), str(settings().max_examples)],
        check=False,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    # A crash reading out of bounds ends the child with a signal or an access violation.
    assert result.returncode == 0, result.stdout + result.stderr
