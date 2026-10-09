from pathlib import Path

from ._arrays import DType, Shape, SupportsDLPack, register_array_type
from ._box import SharedBox, SupportsSharedBox, fields
from ._events import BoxEvents, FieldWatch
from ._layout import Capacity, Field, field
from ._native import (
    BoxClosedError,
    EndOfStream,
    KindMismatchError,
    LockTimeoutError,
    SchemaMismatchError,
    SegmentExistsError,
    SegmentNotFoundError,
    StreamBusyError,
    StreamClosedError,
    WouldBlock,
)
from ._refs import BoxRef, BrokenReferenceError, UnknownBoxClassError
from ._stream import (
    ReaderEvents,
    ReaderStatistics,
    SharedStream,
    StreamReader,
    StreamSender,
    StreamStatistics,
)
from ._version import __version__

__all__ = [
    "BoxClosedError",
    "BoxEvents",
    "BoxRef",
    "BrokenReferenceError",
    "Capacity",
    "DType",
    "EndOfStream",
    "Field",
    "FieldWatch",
    "KindMismatchError",
    "LockTimeoutError",
    "ReaderEvents",
    "ReaderStatistics",
    "SchemaMismatchError",
    "SegmentExistsError",
    "SegmentNotFoundError",
    "Shape",
    "SharedBox",
    "SharedStream",
    "StreamBusyError",
    "StreamClosedError",
    "StreamReader",
    "StreamSender",
    "StreamStatistics",
    "SupportsDLPack",
    "SupportsSharedBox",
    "UnknownBoxClassError",
    "WouldBlock",
    "__version__",
    "field",
    "fields",
    "get_include",
    "register_array_type",
]


def get_include() -> str:
    """Return the folder holding `sharedbox/sharedbox.hpp`, `sharedbox/sharedbox_c.h` and `sharedbox/sharedbox_c.cpp`.

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
