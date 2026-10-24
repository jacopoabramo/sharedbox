"""The script of the guide "How to change several fields at once"."""

# --8<-- [start:update]
from sharedbox import SharedBox


class Parcel(SharedBox, name="example-parcel"):
    width: int = 0
    height: int = 0


parcel = Parcel()
parcel.update(width=3, height=4)
# --8<-- [end:update]

# --8<-- [start:all-or-nothing]
try:
    parcel.update(width=5, height="six")
except TypeError as error:
    print(error)  # Parcel.height expects int, got str
print(parcel.width)  # 3
# --8<-- [end:all-or-nothing]

# --8<-- [start:snapshot]
values = parcel.snapshot()
print(values)  # {'width': 3, 'height': 4}
# --8<-- [end:snapshot]


# --8<-- [start:follow]
class Order(SharedBox, name="example-order"):
    items: int = 0
    parcel: Parcel | None = None


order = Order(2, parcel)
print(order.snapshot())  # {'items': 2, 'parcel': BoxRef(...)}
print(order.snapshot(follow=True))  # {'items': 2, 'parcel': {'width': 3, 'height': 4}}
# --8<-- [end:follow]

order.close()
parcel.close()
Order.unlink()
Parcel.unlink()
