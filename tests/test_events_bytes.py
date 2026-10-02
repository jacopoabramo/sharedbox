import decimal
import math
import queue
from typing import Annotated, Any

import numpy as np

from sharedbox import Capacity, DType, Shape, SharedBox


class Watched(SharedBox):
    grid: Annotated[np.ndarray, Shape(2), DType("float64")] = np.zeros(2)
    price: Annotated[decimal.Decimal, Capacity(8)] = decimal.Decimal(0)
    either: int | bool = 0
    x: float = 0.0
    marker: int = 0


def same(a: Any, b: Any) -> bool:
    if isinstance(a, np.ndarray):
        return bool(np.array_equal(a, b))
    if isinstance(a, decimal.Decimal):
        return str(a) == str(b)
    if isinstance(a, float) and math.isnan(a):
        return math.isnan(b)
    return a == b and type(a) is type(b)


def test_each_stored_change_emits_once_and_the_watcher_keeps_running(
    unique_name: str,
) -> None:
    """Check that the watcher emits once per change of stored bytes, for arrays, sNaN decimals and int | bool, and not for a write of the same bytes."""
    seen: queue.Queue[tuple[str, Any]] = queue.Queue()
    with Watched.create(unique_name) as box:
        box.events.connect(lambda info: seen.put((info.signal.name, info.args[0])))
        steps = [
            ("grid", np.array([1.0, 2.0])),
            ("price", decimal.Decimal("sNaN")),
            ("price", decimal.Decimal("1.0")),
            ("price", decimal.Decimal("1.00")),
            ("either", 1),
            ("either", True),
            ("x", -0.0),
            ("x", math.nan),
        ]
        for name, value in steps:
            setattr(box, name, value)
            got_name, got = seen.get(timeout=5)
            assert got_name == name
            assert same(got, value)
        box.grid = np.array([1.0, 2.0])
        box.x = math.nan
        box.either = True
        box.marker = 1
        assert seen.get(timeout=5) == ("marker", 1)
    Watched.unlink(unique_name)
