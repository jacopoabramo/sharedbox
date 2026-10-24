"""The script of the guide "How to store lists, sets and dicts"."""

# --8<-- [start:declare]
from typing import Annotated

from sharedbox import Capacity, SharedBox, field

Tracking = Annotated[str, Capacity(16)]
Service = Annotated[str, Capacity(8)]


class Van(SharedBox, name="example-van"):
    parcels: Annotated[tuple[Tracking, ...], Capacity(8)] = ()
    services: frozenset[Service] = field(capacity=4, default=frozenset())
    attempts: Annotated[dict[Tracking, int], Capacity(8)] = field(default_factory=dict)


# --8<-- [end:declare]

# --8<-- [start:write]
van = Van()
van.parcels = ("RR123", "RR456")
van.attempts = {"RR123": 3}
print(van.parcels, van.attempts)  # ('RR123', 'RR456') {'RR123': 3}
# --8<-- [end:write]

# --8<-- [start:add]
van.parcels = (*van.parcels, "RR789")
print(len(van.parcels))  # 3
# --8<-- [end:add]

# --8<-- [start:too-many]
try:
    van.services = frozenset({"express", "signed", "insured", "cold", "fragile"})
except ValueError as error:
    print(error)  # Van.services holds at most 4 elements; the value has 5
# --8<-- [end:too-many]

van.close()
Van.unlink()
