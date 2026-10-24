import contextlib
import os
import subprocess
import sys
from pathlib import Path

from os_names import os_name

SCRIPT = Path(__file__).resolve().parent.parent / "docs" / "tutorials" / "mailbox.py"
NAMES = ("tutorial-mailbox", "tutorial-north", "tutorial-south", "tutorial-carrier")


def test_the_tutorial_script_prints_what_its_pages_show(tmp_path: Path) -> None:
    """Check that the tutorial script runs and prints the lines the tutorial pages show."""
    run = os.environ["SHAREDBOX_TEST_RUN"]
    source = SCRIPT.read_text(encoding="utf-8")
    assert all(f'"{name}"' in source for name in NAMES)
    script = tmp_path / "mailbox.py"
    script.write_text(
        source.replace('"tutorial-', f'"tutorial-{run}-'), encoding="utf-8"
    )
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        if sys.platform.startswith("linux"):
            for name in NAMES:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(f"/dev/shm/{os_name(f'tutorial-{run}-{name[9:]}')}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "letters left by the carrier: 2",
        "letters 0 -> 3",
        "watched 3",
        "letters at the stop 1",
        "letters at the stop 7",
    ]
