"""UI RPC handlers.

OP-25h | py_modules/unifideck/rpc/handlers/ui.py
"""
from __future__ import annotations

import logging
from typing import Any

from unifideck.core.types.events import Events
from unifideck.rpc.errors import RpcError
from unifideck.rpc.handlers.base import RpcHandlerBase

logger = logging.getLogger(__name__)

_CLOUD_FAILURE_STORES = ("default", "epic", "gog", "amazon", "ubisoft")
_CLOUD_FAILURE_MODES = ("silent", "toast")


class UIHandlers(RpcHandlerBase):
    """CDP injection, game metadata, language, cloud failure behaviours."""

    config_validation_result: Any = None

    async def notify_game_launched(
        self, store: str, game_id: str, **kw: Any,
    ) -> Any:
        """Emit GAME_LAUNCHED event to the bus."""
        await self._bus.emit(
            Events.GAME_LAUNCHED, store=store, game_id=game_id, **kw,
        )

    async def notify_game_stopped(
        self, store: str, game_id: str, exit_code: int = 0,
    ) -> Any:
        """Emit GAME_STOPPED event with exit code."""
        await self._bus.emit(
            Events.GAME_STOPPED,
            store=store,
            game_id=game_id,
            exit_code=exit_code,
        )

    async def hide_play_section(self, app_id: int) -> Any:
        """Inject CSS hiding a game's Play button in Steam UI."""
        cdp = self._require(getattr(self._services, "cdp", None), "cdp")
        return await cdp.hide_play_section(app_id)

    async def unhide_play_section(self, app_id: int) -> Any:
        """Remove the hide-play-section CSS injection."""
        cdp = self._require(getattr(self._services, "cdp", None), "cdp")
        return await cdp.unhide_play_section(app_id)

    async def inject_hide_css(self, app_id: int, css: str) -> Any:
        """Inject arbitrary CSS keyed by app_id."""
        cdp = self._require(getattr(self._services, "cdp", None), "cdp")
        return await cdp.inject_css(app_id, css)

    async def get_game_metadata(self, store: str, game_id: str) -> Any:
        """Return merged metadata for a game from the sync cache."""
        metadata = self._require(
            getattr(self._services, "metadata", None), "metadata",
        )
        return await metadata.get(store, game_id)

    async def get_language_preference(self) -> Any:
        """Return the current UI locale config value."""
        return {"locale": self._config.get("ui.locale", "en-US")}

    async def set_language_preference(self, locale: str) -> Any:
        """Persist the UI locale via config."""
        self._config.set("ui.locale", locale)
        return {"locale": locale}

    async def get_cloud_failure_behaviors(self) -> Any:
        """Return per-store cloud failure behavior map."""
        return {
            store: self._config.get(
                f"cloud.failure_behavior.{store}", "toast",
            )
            for store in _CLOUD_FAILURE_STORES
        }

    async def set_cloud_failure_behavior(
        self, store: str, value: str,
    ) -> Any:
        """Persist a per-store cloud failure behavior override."""
        if store not in _CLOUD_FAILURE_STORES:
            raise RpcError(
                "unsupported_store",
                f"Must be one of {_CLOUD_FAILURE_STORES}",
                store=store,
            )
        if value not in _CLOUD_FAILURE_MODES:
            raise RpcError(
                "invalid_behavior",
                f"Must be one of {_CLOUD_FAILURE_MODES}",
                value=value,
            )
        self._config.set(f"cloud.failure_behavior.{store}", value)
        return {"store": store, "value": value}

    async def get_config_validation_status(self) -> Any:
        """Expose the boot-time config validation result to the UI."""
        if self.config_validation_result is None:
            return {"degraded": False, "errors": [], "warnings": []}
        return self.config_validation_result
