"""stores/shared/store_base.py — Abstract base class for all store plugins.
# OP-47b | stores/shared/store_base.py | Depends: OP-05

6 abstract methods + 4 provided utilities. Zero plugin_instance refs.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

from ...core.types import AuthResult, Game, InstallResult, Result, StoreInfo

if TYPE_CHECKING:
    from ...core.types import CLITool
    from ...event_bus.event_bus import EventBus
    from ...core.bin.binary_resolver import BinaryResolver


class StoreBase(ABC):
    """Abstract base for every store connector.

    Subclasses implement 6 abstract methods. StoreBase provides:
    - _find_binary(tool) via BinaryResolver
    - _find_exe(install_path) via ExeFinder
    - _emit(event, **payload) via EventBus
    - _on_auth_success() auto-trigger
    """

    store_info: StoreInfo  # must be set as class attr by subclass

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if authenticated and CLI available."""

    @abstractmethod
    async def start_auth(self) -> AuthResult:
        """Begin OAuth / credential flow."""

    @abstractmethod
    async def complete_auth(self, code: str) -> AuthResult:
        """Complete auth with code/2FA."""

    @abstractmethod
    async def logout(self) -> Result:
        """Clear stored credentials."""

    @abstractmethod
    async def get_library(self) -> list[Game] | None:
        """Fetch owned game library from store API."""

    # Provided utilities ─────────────────────────────────────────────

    def _find_binary(self, tool: CLITool) -> str | None:
        """Locate CLI binary via shared BinaryResolver."""
        raise NotImplementedError("OP-47b: binary_resolver.resolve(tool)")

    def _find_exe(self, install_path: str, hints: list[str] | None = None) -> str | None:
        """Locate game executable via shared ExeFinder."""
        raise NotImplementedError("OP-47b: exe_finder.find(install_path, hints)")

    def _emit(self, event: str, **payload: Any) -> None:
        """Emit an event on the shared EventBus."""
        raise NotImplementedError("OP-47b: self._bus.emit(event, **payload)")

    async def _on_auth_success(self) -> None:
        """Emit AUTH_COMPLETE after successful auth. Called by subclasses."""
        raise NotImplementedError("OP-47b: emit AUTH_COMPLETE event")

    @property
    def store_name(self) -> str:
        """Return the store's identifier string."""
        return self.store_info.name
