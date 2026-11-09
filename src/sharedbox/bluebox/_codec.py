"""Documents as CBOR: numpy arrays travel as RFC 8746 typed arrays."""

import enum
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import cbor2
import numpy as np

_TAGS = {
    "u1": 64,
    "u2": 69,
    "u4": 70,
    "u8": 71,
    "i1": 72,
    "i2": 77,
    "i4": 78,
    "i8": 79,
    "f2": 84,
    "f4": 85,
    "f8": 86,
}
_DTYPES = {tag: np.dtype("<" + code) for code, tag in _TAGS.items()}
_MULTI_DIMENSIONAL = 40


def encode_default(encoder: cbor2.CBOREncoder, value: object) -> None:
    """Encode what `cbor2` does not: arrays, numpy scalars and enum members.

    An array or numpy scalar is written as a little-endian RFC 8746 typed
    array, inside a multi-dimensional array (tag 40) unless it has one
    dimension. A numpy `float64` is a `float` and is written as one. An
    enum member is written as its value.

    Raises
    ------
    TypeError
        If `value` is of another type, or an array of a dtype without a typed
        array tag (only 1 to 8 byte integers and 2 to 8 byte floats have one).
    """
    if isinstance(value, np.ndarray | np.generic):
        array = np.asarray(value)
        tag = _TAGS.get(f"{array.dtype.kind}{array.dtype.itemsize}")
        if tag is None:
            raise TypeError(f"no typed array tag for dtype {array.dtype}")
        data = array.astype(array.dtype.newbyteorder("<"), copy=False).tobytes()
        typed = cbor2.CBORTag(tag, data)
        if array.ndim == 1:
            encoder.encode(typed)
        else:
            encoder.encode(cbor2.CBORTag(_MULTI_DIMENSIONAL, [list(array.shape), typed]))
    elif isinstance(value, enum.Enum):
        encoder.encode(value.value)
    else:
        raise TypeError(f"cannot encode a {type(value).__name__}")


def decode_tag_hook(tag: cbor2.CBORTag, immutable: bool) -> Any:
    """Turn the typed arrays `encode_default` writes back into arrays.

    A multi-dimensional array with no dimensions comes back as a numpy scalar
    of the array's dtype. Any other tag is returned unchanged.
    """
    if tag.tag in _DTYPES:
        return np.frombuffer(tag.value, _DTYPES[tag.tag]).copy()
    if tag.tag == _MULTI_DIMENSIONAL:
        shape, array = tag.value
        array = array.reshape(shape)
        return array[()] if array.ndim == 0 else array
    return tag


def _unsupported(value: object, path: tuple[str, ...]) -> Iterator[tuple[str, object]]:
    match value:
        case dict():
            for key, item in value.items():
                yield from _unsupported(item, (*path, str(key)))
        case list() | tuple():
            for index, item in enumerate(value):
                yield from _unsupported(item, (*path, str(index)))
        case _:
            try:
                cbor2.dumps(value, default=encode_default)
            except (TypeError, cbor2.CBORError):
                yield ".".join(path), value


def encode_document(name: str, doc: Mapping[str, object]) -> bytes:
    """Return the canonical CBOR of the pair `[name, doc]`.

    Raises
    ------
    TypeError
        If `doc` holds a value that `encode_default` cannot write. The
        message names the document and the dotted path of the key.
    """
    try:
        return cbor2.dumps([name, doc], canonical=True, default=encode_default)
    except (TypeError, cbor2.CBORError):
        for path, value in _unsupported(dict(doc), ()):
            raise TypeError(
                f"{name} document: key {path!r} holds a {type(value).__name__} "
                "that cannot be encoded"
            ) from None
        raise


def decode_document(data: bytes) -> tuple[str, dict[str, object]]:
    """Return the name and the document that `encode_document` wrote."""
    name, doc = cbor2.loads(data, tag_hook=decode_tag_hook)
    return name, doc


def split_parts(data: bytes, size: int) -> list[bytes]:
    """Return `data` in parts of `size` bytes, the last one shorter, or one empty part."""
    return [data[start : start + size] for start in range(0, len(data), size)] or [b""]
