import os
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

import pytest

EXAMPLES = sorted(
    (Path(__file__).resolve().parent.parent / "docs" / "examples").glob("*.py")
)


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda path: path.stem)
def test_an_example_script_runs(script: Path, tmp_path: Path) -> None:
    """Check that each script a how-to guide shows runs to the end and, on Linux, leaves no segment."""
    if (
        script.stem in {"store_arrays", "store_numpy_numbers"}
        and find_spec("numpy") is None
    ):
        pytest.skip("the guide needs numpy")
    prefix = f"example-{os.environ['SHAREDBOX_TEST_RUN']}-{script.stem}-"
    copy = tmp_path / script.name
    copy.write_text(
        script.read_text(encoding="utf-8").replace('"example-', f'"{prefix}'),
        encoding="utf-8",
    )
    subprocess.run([sys.executable, str(copy)], timeout=120, check=True)
    # Only Linux keeps a segment nobody unlinked; Windows frees it with its last handle.
    if sys.platform == "linux":
        assert sorted(Path("/dev/shm").glob(f"SBX:{prefix}*")) == []
