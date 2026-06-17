from abc import ABC, abstractmethod

class CloudSaveStrategy(ABC):
    """Abstract base class for store-specific cloud save synchronization strategies."""

    @abstractmethod
    def get_local_save_dir(self, game_id: str) -> str | None:
        """Resolve and return the game's actual local save directory.

        Returns None if it cannot be resolved.
        """
        pass

    @abstractmethod
    async def sync_down(self, game_id: str, force: bool = False) -> bool:
        """Synchronize cloud save files from the store cloud to the local path.

        When ``force`` is True, the cloud copy is pulled unconditionally
        (overriding the store tool's local-newer/same-age "skip" decision) —
        used for the explicit "Use Cloud" conflict resolution. Returns True on
        success, False otherwise.
        """
        pass

    @abstractmethod
    async def sync_up(self, game_id: str) -> bool:
        """Synchronize cloud save files from the local path to the store cloud.

        Returns True on success, False otherwise.
        """
        pass
