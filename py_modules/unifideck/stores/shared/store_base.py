import asyncio
import logging
import os
import subprocess
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from unifideck.core.binaries import binary_resolver
from unifideck.core.exe_finder import exe_finder
from unifideck.core.types import (
    AuthResult,
    CLITool,
    Events,
    Game,
    InstallResult,
    Result,
    StoreError,
    StoreInfo,
)

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.core.cache_manager import CacheManager
    from unifideck.event_bus import EventBus
logger = logging.getLogger(__name__)
class StoreBase(ABC):
    """Store base."""
    store_info: StoreInfo = StoreInfo(
        name="unknown",
        display_name="Unknown",
        auth_method="manual",
        icon_asset="",
    )
    def __init__(
        self,
        bus: "EventBus",
        cache: "CacheManager",
        plugin_dir: str | None = None,
        config: Optional["ConfigManager"] = None,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._cache = cache
        self._plugin_dir = plugin_dir
        self._config = config
        self._cached_available: bool = False
    @property
    def store_name(self) -> str:
        """Store name."""
        return self.store_info.name
    @abstractmethod
    async def is_available(self) -> bool:
        """Check whether available."""
        ...
    @abstractmethod
    async def start_auth(self, **kwargs: Any) -> AuthResult:
        """Start auth."""
        ...
    @abstractmethod
    async def complete_auth(self, **kwargs: Any) -> AuthResult:
        """Complete auth."""
        ...
    @abstractmethod
    async def logout(self) -> Result:
        """Logout."""
        ...
    @abstractmethod
    async def get_library(self) -> list[Game] | None:
        """Get library."""
        ...

    @abstractmethod
    async def install_game(
        self, game_id: str, **kwargs: Any,
    ) -> InstallResult:
        """Install game."""
        ...
    @abstractmethod
    async def uninstall_game(
        self, game_id: str, **kwargs: Any,
    ) -> Result:
        """Uninstall game."""
        ...
    @abstractmethod
    async def update_game(
        self, game_id: str, **kwargs: Any,
    ) -> InstallResult:
        """Update game."""
        ...
    @abstractmethod
    async def check_for_updates(self) -> list[str]:
        """Check for updates."""
        ...
    @abstractmethod
    async def get_game_size(self, game_id: str) -> int | None:
        """Get game size."""
        ...
    def _find_binary(self, tool: CLITool) -> str | None:
        """Find binary."""
        return binary_resolver.resolve(tool)
    def _find_exe(
        self,
        install_path: str,
        hints: list[str] | None = None,
    ) -> str | None:
        """Find exe."""
        return exe_finder.find(install_path, hints)
    async def _emit(self, event: Events, **kwargs: Any) -> None:
        """Emit a bus event with arbitrary kwargs payload."""
        await self._bus.emit(event, **kwargs)

    async def _run_cli(
        self,
        args: list[str],
        binary_path: str | None = None,
        timeout: int = 300,
        env: dict[str, str] | None = None,
    ) -> str:

        """Run cli."""
        bin_path = binary_path or getattr(self, "cli_path", None)
        if not bin_path:
            raise StoreError(
                "CLI binary not found",
                store=self.store_name,
            )
        cmd = [bin_path, *args]
        process_env = (
            dict(os.environ) if env is None
            else {**os.environ, **env}
        )
        def _run() -> str:
            """Run the subprocess synchronously, return stdout."""
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=process_env,
                check=False,  # rc read manually below to raise StoreError
            )
            if result.returncode != 0:
                raise StoreError(
                    f"CLI error (rc={result.returncode}): "
                    f"{result.stderr[:500]}",
                    store=self.store_name,
                )
            return result.stdout
        try:
            return await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired as e:
            raise StoreError(
                f"CLI timeout after {timeout}s: {' '.join(cmd[:3])}",
                store=self.store_name,
            ) from e
        except StoreError:
            raise
        except Exception as e:
            raise StoreError(
                f"CLI execution failed: {e}",
                store=self.store_name,
            ) from e
