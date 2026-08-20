import asyncio
import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from unifideck.core.binaries import binary_resolver, clean_cli_env
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
    async def get_library(self, *, force: bool = False) -> list[Game] | None:
        """Get library (``force`` requests a cache-bypassing refresh)."""
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

    async def get_installed_path(self, game_id: str) -> str | None:
        """Resolve the on-disk install directory for an installed game.

        Used to compute the exact "Installed size" when the sync cache's
        ``install_path`` is missing or stale. Default ``None`` (unknown);
        stores that track installs locally override this — e.g. Epic
        reads legendary's ``installed.json``.
        """
        return None

    def get_prefix_path(self, game_id: str) -> str | None:
        """The Wine prefix a game lives in, for stores where that is the install.

        Only meaningful for a **wrapper store**: its vendor client runs inside
        the prefix and installs the game into it, so the prefix is the game's
        real footprint and what uninstalling reclaims. Every other store
        downloads outside its prefix and leaves this ``None`` — the default —
        which is what keeps ``resolve_size_root`` a shared rule keyed on
        ``prefix_owns_game_install`` rather than a store-name branch.

        Synchronous: for both wrapper stores this is an in-memory id-map read,
        and it is called from a size lookup that is already off the hot path.
        """
        return None
    def _find_binary(self, tool: CLITool) -> str | None:
        """Find binary.

        The shared :class:`BinaryResolver` Tier-1 lookup requires
        every entry in ``tool.search_paths`` to be absolute (it
        rejects relative paths via ``Path.is_absolute()``). Stores
        idiomatically declare *relative* search paths like
        ``"bin/legendary"`` so the descriptor stays portable across
        install layouts. Absolutise them against ``self._plugin_dir``
        before delegating — otherwise the bundled CLI in
        ``<plugin>/bin/`` is silently skipped and the resolver
        falls through to ``PATH`` / ``~/.local/bin`` where the
        binary doesn't exist.
        """
        if self._plugin_dir:
            absolutised = [
                p if Path(p).is_absolute()
                else str(Path(self._plugin_dir) / p)
                for p in tool.search_paths
            ]
            tool = CLITool(
                name=tool.name,
                search_paths=absolutised,
                version_flag=tool.version_flag,
                min_version=tool.min_version,
            )
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
        timeout: int = 300,  # noqa: ASYNC109 — timeout is API value passed to underlying lib (urllib/aiohttp/subprocess), not an asyncio.timeout() wrapper
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
        # Start from a scrubbed env, not raw os.environ: the frozen Decky
        # loader leaks LD_LIBRARY_PATH=/tmp/_MEIxxxx into every child, which
        # a zipapp CLI (legendary/gogdl) obeys and a PyInstaller ELF ignored.
        process_env = clean_cli_env(env)
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
