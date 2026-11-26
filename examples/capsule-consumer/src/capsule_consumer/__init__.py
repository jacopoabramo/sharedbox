"""Send sharedbox stream items from a C++ thread, at a rate a shared box sets."""

from sharedbox import get_schema_hash

from . import _producer
from ._producer import Producer
from ._types import Sample, Settings

_producer.set_schemas(get_schema_hash(Settings), get_schema_hash(Sample))

__all__ = ["Producer", "Sample", "Settings"]
