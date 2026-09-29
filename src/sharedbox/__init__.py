from pathlib import Path

from ._box import SharedBox, SupportsSharedBox, fields
from ._events import FieldWatch
from ._layout import Capacity, Field, field
from ._native import (
    BoxClosedError,
    LockTimeoutError,
    SchemaMismatchError,
    SegmentExistsError,
    SegmentNotFoundError,
)
from ._refs import BrokenReferenceError, UnknownBoxClassError

__all__ = [
    "BoxClosedError",
    "BrokenReferenceError",
    "Capacity",
    "Field",
    "FieldWatch",
    "LockTimeoutError",
    "SchemaMismatchError",
    "SegmentExistsError",
    "SegmentNotFoundError",
    "SharedBox",
    "SupportsSharedBox",
    "UnknownBoxClassError",
    "field",
    "fields",
    "get_include",
]


def get_include() -> str:
    """Folder holding `sharedbox/sharedbox.hpp`, `sharedbox_c.h` and `sharedbox_c.cpp`.

    Add it to the include path of an extension that uses a box through its capsule.

    Raises
    ------
    FileNotFoundError
        If `sharedbox.hpp` is not installed with this copy of sharedbox.
    """
    for folder in __path__:
        include = Path(folder) / "include"
        if (include / "sharedbox" / "sharedbox.hpp").is_file():
            return str(include)
    raise FileNotFoundError(
        "sharedbox.hpp is not installed with this copy of sharedbox"
    )
