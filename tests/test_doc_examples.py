import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = sorted(
    (Path(__file__).resolve().parent.parent / "docs" / "examples").glob("*.py")
)


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda path: path.stem)
def test_an_example_script_runs(script: Path, tmp_path: Path) -> None:
    """Check that each script a how-to guide shows runs to the end without error."""
    run = os.environ["SHAREDBOX_TEST_RUN"]
    copy = tmp_path / script.name
    copy.write_text(
        script.read_text(encoding="utf-8").replace('"example-', f'"example-{run}-'),
        encoding="utf-8",
    )
    subprocess.run([sys.executable, str(copy)], timeout=120, check=True)
