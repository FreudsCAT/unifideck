"""UI RPC mixin for Plugin class.

OP-26g | rpc/mixins/ui.py
"""
from __future__ import annotations

from typing import Any

from unifideck.rpc.errors import RpcError


class UIRPCMixin:
    """CDP injection, game metadata, and language preferences."""

    config: Any
    services: Any

    async def get_game_metadata(self, store: str, game_id: str) -> Any:
        """Return merged metadata for a game from the sync cache."""
        metadata = getattr(self.services, "metadata", None)
        if metadata is None:
            raise RpcError("service_unavailable", service="metadata")
        return await metadata.get(store, game_id)

    async def hide_play_section(self, app_id: int) -> Any:
        """Inject CSS hiding a game's Play button in Steam UI."""
        cdp = getattr(self.services, "cdp", None)
        if cdp is None:
            raise RpcError("service_unavailable", service="cdp")
        return await cdp.hide_play_section(app_id)

    async def unhide_play_section(self, app_id: int) -> Any:
        """Remove the hide-play-section CSS injection."""
        cdp = getattr(self.services, "cdp", None)
        if cdp is None:
            raise RpcError("service_unavailable", service="cdp")
        return await cdp.unhide_play_section(app_id)

    async def inject_hide_css(self, app_id: int, css: str) -> Any:
        """Inject arbitrary CSS keyed by app_id."""
        cdp = getattr(self.services, "cdp", None)
        if cdp is None:
            raise RpcError("service_unavailable", service="cdp")
        return await cdp.inject_css(app_id, css)

    async def get_language_preference(self) -> Any:
        """Return the current UI locale config value."""
        return {"locale": self.config.get("ui.locale", "en-US")}

    async def set_language_preference(self, locale: str) -> Any:
        """Persist the UI locale via config."""
        self.config.set("ui.locale", locale)
        return {"locale": locale}
