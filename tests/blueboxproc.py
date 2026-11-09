"""Functions a spawned process runs against a bluebox channel."""

import os

import event_model

from sharedbox.bluebox import DocumentSender


def start_run_and_exit(name: str) -> None:
    """Create the channel `name`, send a start and a descriptor, and exit without closing."""
    sender = DocumentSender.create(name)
    bundle = event_model.compose_run()
    sender("start", bundle.start_doc)
    descriptor = bundle.compose_descriptor(
        name="primary", data_keys={"x": {"source": "x", "dtype": "number", "shape": []}}
    )
    sender("descriptor", descriptor.descriptor_doc)
    os._exit(0)
