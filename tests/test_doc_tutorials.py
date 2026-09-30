import contextlib
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "docs" / "tutorials" / "motor.py"
NAMES = ("tutorial-motor", "tutorial-x", "tutorial-y", "tutorial-stage")


def test_the_tutorial_script_prints_what_its_pages_show(tmp_path: Path) -> None:
    """Check that the tutorial script runs and prints the lines the tutorial pages show."""
    run = os.environ["SHAREDBOX_TEST_RUN"]
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.count('"tutorial-') >= len(NAMES)
    script = tmp_path / "motor.py"
    script.write_text(
        source.replace('"tutorial-', f'"tutorial-{run}-'), encoding="utf-8"
    )
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
    finally:
        if sys.platform.startswith("linux"):
            for name in NAMES:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(f"/dev/shm/sharedbox.tutorial-{run}-{name[9:]}")
    lines = result.stdout.splitlines()
    assert lines[0] == "position set by the other process: 10"
    assert set(lines[1:3]) == {"position 0 -> 20", "watched 20"}
    assert lines[3:] == ["motor position 1", "motor position 7"]
