"""The signature a box class should have, built from a dataclass with the same fields."""

import inspect


def box_signature(plain: type) -> str:
    """Return the signature of the dataclass `plain` followed by the keyword `metadata`, as a box with its fields has."""
    signature = inspect.signature(plain)
    metadata = inspect.Parameter(
        "metadata", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=object
    )
    return str(signature.replace(parameters=[*signature.parameters.values(), metadata]))
