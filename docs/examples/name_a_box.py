"""The script of the guide "How to name a box"."""

# --8<-- [start:class-name]
from sharedbox import SegmentExistsError, SharedBox


class Motor(SharedBox, identity="example-motor"):
    position: int = 0


motor = Motor()
same = Motor.attach()
print(same.name == motor.name)  # True
# --8<-- [end:class-name]


# --8<-- [start:fixed-name]
class Settings(SharedBox, name="example-settings"):
    rate: float = 20.0


settings = Settings()
print(settings.name)  # example-settings
# --8<-- [end:fixed-name]

# --8<-- [start:each-box]
x_axis = Motor.create("example-x-axis")
y_axis = Motor.create("example-y-axis", 5)
print(Motor.attach("example-y-axis").position)  # 5
# --8<-- [end:each-box]

# --8<-- [start:taken]
try:
    Motor.create("example-x-axis")
except SegmentExistsError as error:
    print(error)  # a segment named 'example-x-axis' already exists; its creator, ...
# --8<-- [end:taken]

for box in (motor, same, settings, x_axis, y_axis):
    box.close()
Motor.unlink()
Settings.unlink()
Motor.unlink("example-x-axis")
Motor.unlink("example-y-axis")
