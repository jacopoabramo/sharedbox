"""The script of the guide "How to name a box"."""

# --8<-- [start:class-name]
from sharedbox import SegmentExistsError, SharedBox


class Mailbox(SharedBox, identity="example-mailbox"):
    letters: int = 0


mailbox = Mailbox()
same = Mailbox.attach()
print(same.name == mailbox.name)  # True
# --8<-- [end:class-name]


# --8<-- [start:fixed-name]
class Settings(SharedBox, name="example-settings"):
    opening_hour: int = 8


settings = Settings()
print(settings.name)  # example-settings
# --8<-- [end:fixed-name]

# --8<-- [start:each-box]
north = Mailbox.create("example-north")
south = Mailbox.create("example-south", 5)
south_view = Mailbox.attach("example-south")
print(south_view.letters)  # 5
# --8<-- [end:each-box]

# --8<-- [start:taken]
try:
    Mailbox.create("example-north")
except SegmentExistsError as error:
    print(error)  # a segment named 'example-north' already exists; its creator, ...
# --8<-- [end:taken]

for box in (mailbox, same, settings, north, south, south_view):
    box.close()
Mailbox.unlink()
Settings.unlink()
Mailbox.unlink("example-north")
Mailbox.unlink("example-south")
