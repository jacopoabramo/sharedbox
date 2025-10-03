

class SharedDict:
    def __init__(self, name: str, data: object | None = None, size: int = 134217728, create: bool = True, max_keys: int = 128) -> None:
        """Create or open a shared memory dictionary"""

    def close(self) -> None:
        """Close access to shared memory without removing it"""

    def unlink(self) -> None:
        """Remove the shared memory segment entirely"""

    def is_closed(self) -> bool:
        """Check if this SharedDict connection has been closed"""

    def __len__(self) -> int: ...

    def __contains__(self, arg: str, /) -> bool: ...

    def __getitem__(self, arg: str, /) -> object: ...

    def __setitem__(self, arg0: str, arg1: object, /) -> None: ...

    def __delitem__(self, arg: str, /) -> None: ...

    def get(self, key: str, default: object | None = None) -> object: ...

    def keys(self) -> list:
        """Return list of all keys"""

    def keys_atomic(self) -> list:
        """Get keys with full atomic snapshot (locks all stripes at once)"""

    def values(self) -> list:
        """Return list of all values"""

    def items(self) -> list:
        """Return list of (key, value) tuples"""

    def get_stats(self) -> dict:
        """Get runtime statistics and diagnostic information"""

    def recommend_sizing(self, target_entries: object | None = None) -> dict:
        """Get sizing recommendations based on current usage"""
