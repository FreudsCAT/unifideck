"""UIRPCMixin — Steam-UI manipulation + locale RPC (subset of UIHandlers).

OP-26g | py_modules/unifideck/rpc/mixins/ui.py

Mixin form of a subset of ``UIHandlers`` (OP-25h):

* metadata read;
* CDP-driven Steam UI manipulation (hide/unhide play section,
  inject CSS);
* locale read/write.

The CDP methods here go through a dedicated ``services.cdp``
facade rather than reaching for ``get_cdp_client`` directly —
the older composition pattern.
"""

from __future__ import annotations

from typing import Any


class UIRPCMixin:
    """UI-side RPC: metadata, CDP manipulation, locale preference."""

    config: Any
    services: Any

    async def get_game_metadata(self, store: str, game_id: str) -> Any:
        """Return enriched metadata for one game.

        Delegates to ``services.metadata.get`` — older API
        than the handler-group version which does a manual
        sync-service lookup + enrich.

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            Metadata dict from the metadata service.
        """
        return await self.services.metadata.get(store, game_id)

    async def hide_play_section(self, app_id: int) -> Any:
        """Hide the "Play" section of a Steam game tile via CDP injection.

        Used for games where Unifideck wants to replace
        Steam's native "Play" button. Delegates to the CDP
        facade.

        Args:
            app_id: Steam AppID.

        Returns:
            Whatever the CDP facade returns (typically a
            bool or status dict).
        """
        return await self.services.cdp.hide_play_section(app_id)

    async def unhide_play_section(self, app_id: int) -> Any:
        """Restore Steam's native "Play" section after a hide.

        Args:
            app_id: Steam AppID.

        Returns:
            Whatever the CDP facade returns.
        """
        return await self.services.cdp.unhide_play_section(app_id)

    async def inject_hide_css(self, app_id: int, css: str) -> Any:
        """Inject custom CSS for an app via the CDP facade.

        Args:
            app_id: Steam AppID (passed as a key for the
                CDP layer to dedupe/replace prior
                injections).
            css: raw CSS string.

        Returns:
            Whatever the CDP facade returns.
        """
        return await self.services.cdp.inject_css(app_id, css)

    async def get_language_preference(self) -> Any:
        """Return the currently-configured UI locale (default ``"en-US"``).

        Differs from ``UIHandlers.get_language_preference``
        only in providing a fallback default — the handler
        group returns whatever the config returns (possibly
        ``None``).

        Returns:
            ``{locale: <str>}``.
        """
        return {"locale": self.config.get("ui.locale", "en-US")}

    async def set_language_preference(self, locale: str) -> Any:
        """Persist a new UI locale to the config.

        No validation against a list of known locales —
        the frontend is canonical on what locales exist.

        Args:
            locale: locale code (e.g. ``"en-US"``,
                ``"fr-FR"``).

        Returns:
            ``{success: True, locale}``.
        """
        self.config.set("ui.locale", locale)
        return {"success": True, "locale": locale}
