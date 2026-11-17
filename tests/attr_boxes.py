"""Boxes with attribute fields that several test modules use."""

from typing import Annotated

import numpy as np

from sharedbox import Attr, Capacity, DType, MaxShape, Setting, SharedBox, attr

CLOCK_SLACK_NS = 20_000_000


class Camera(SharedBox):
    temperature: Attr[float] = attr()
    exposure: Setting[float] = attr()
    move: Attr[float, [float, Annotated[str, Capacity(16)]]] = attr()
    home: Attr[None] = attr()


class Frames(SharedBox):
    image: Setting[Annotated[np.ndarray, MaxShape((4, 4)), DType(np.uint8)]] = attr()
