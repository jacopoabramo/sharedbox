"""The script of the guide "How to store lists, sets and dicts"."""

# --8<-- [start:declare]
from typing import Annotated

from sharedbox import Capacity, SharedBox, field

Title = Annotated[str, Capacity(32)]


class Playlist(SharedBox, name="example-playlist"):
    tracks: Annotated[tuple[Title, ...], Capacity(8)] = ()
    tags: Annotated[frozenset[Annotated[str, Capacity(8)]], Capacity(4)] = frozenset()
    plays: Annotated[dict[Title, int], Capacity(8)] = field(default_factory=dict)


# --8<-- [end:declare]

# --8<-- [start:write]
playlist = Playlist()
playlist.tracks = ("intro", "theme")
playlist.plays = {"intro": 3}
print(playlist.tracks, playlist.plays)  # ('intro', 'theme') {'intro': 3}
# --8<-- [end:write]

# --8<-- [start:add]
playlist.tracks = (*playlist.tracks, "outro")
print(len(playlist.tracks))  # 3
# --8<-- [end:add]

# --8<-- [start:too-many]
try:
    playlist.tags = frozenset({"a", "b", "c", "d", "e"})
except ValueError as error:
    print(error)  # Playlist.tags holds at most 4 elements; the value has 5
# --8<-- [end:too-many]

playlist.close()
Playlist.unlink()
