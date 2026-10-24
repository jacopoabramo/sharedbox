"""The script of the guide "How to store text and bytes"."""

# --8<-- [start:declare]
from typing import Annotated

from sharedbox import Capacity, SharedBox, field


class Envelope(SharedBox, name="example-envelope"):
    owner: Annotated[str, Capacity(16)] = ""
    barcode: bytes = field(capacity=1024, default=b"")


# --8<-- [end:declare]

# --8<-- [start:write]
envelope = Envelope()
envelope.owner = "Ada"
envelope.barcode = bytes(range(8))
print(envelope.owner, len(envelope.barcode))  # Ada 8
# --8<-- [end:write]

# --8<-- [start:too-long]
try:
    envelope.owner = "Ada Lovelace, 12 High St"
except ValueError as error:
    print(error)  # Envelope.owner holds at most 16 bytes; the value encodes to 24
print(envelope.owner)  # Ada
# --8<-- [end:too-long]

# --8<-- [start:bytes-not-characters]
print(len("été"), len("été".encode()))  # 3 5
# --8<-- [end:bytes-not-characters]


# --8<-- [start:fit]
def fit(text: str, size: int) -> str:
    return text.encode()[:size].decode(errors="ignore")


envelope.owner = fit("Ada Lovelace, 12 High St", 16)
print(envelope.owner)  # Ada Lovelace, 12
# --8<-- [end:fit]

envelope.close()
Envelope.unlink()
