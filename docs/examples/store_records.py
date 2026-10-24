"""The script of the guide "How to store records"."""

# --8<-- [start:declare]
from dataclasses import dataclass
from typing import Annotated, NamedTuple, NotRequired, TypedDict

from sharedbox import Capacity, SharedBox, field


@dataclass(frozen=True)
class Address:
    street: Annotated[str, Capacity(32)]
    number: int
    city: Annotated[str, Capacity(32)]


class Hours(NamedTuple):
    opens: int
    closes: int


class Instructions(TypedDict):
    attempts: int
    note: NotRequired[Annotated[str, Capacity(16)]]


class Recipient(SharedBox, name="example-recipient"):
    address: Address = Address("", 0, "")
    hours: Hours = Hours(8, 18)
    instructions: Instructions = field(default_factory=lambda: {"attempts": 1})


# --8<-- [end:declare]

# --8<-- [start:write]
recipient = Recipient()
recipient.address = Address("High Street", 12, "Leeds")
print(recipient.address)  # Address(street='High Street', number=12, city='Leeds')
# --8<-- [end:write]

# --8<-- [start:change-one-member]
recipient.hours = recipient.hours._replace(closes=17)
print(recipient.hours)  # Hours(opens=8, closes=17)
# --8<-- [end:change-one-member]

# --8<-- [start:optional-key]
print(recipient.instructions)  # {'attempts': 1}
recipient.instructions = {"attempts": 2, "note": "leave at door"}
print(recipient.instructions["note"])  # leave at door
# --8<-- [end:optional-key]

recipient.close()
Recipient.unlink()
