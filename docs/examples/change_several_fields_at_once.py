"""The script of the guide "How to change several fields at once"."""

# --8<-- [start:update]
from sharedbox import SharedBox


class Point(SharedBox, name="example-point"):
    x: int = 0
    y: int = 0


point = Point()
point.update(x=3, y=4)
# --8<-- [end:update]

# --8<-- [start:all-or-nothing]
try:
    point.update(x=5, y="six")
except TypeError as error:
    print(error)  # Point.y expects int, got str
print(point.x)  # 3
# --8<-- [end:all-or-nothing]

# --8<-- [start:snapshot]
values = point.snapshot()
print(values)  # {'x': 3, 'y': 4}
# --8<-- [end:snapshot]


# --8<-- [start:follow]
class Probe(SharedBox, name="example-probe"):
    depth: int = 0
    point: Point | None = None


probe = Probe(2, point)
print(probe.snapshot())  # {'depth': 2, 'point': BoxRef(...)}
print(probe.snapshot(follow=True))  # {'depth': 2, 'point': {'x': 3, 'y': 4}}
# --8<-- [end:follow]

probe.close()
point.close()
Probe.unlink()
Point.unlink()
