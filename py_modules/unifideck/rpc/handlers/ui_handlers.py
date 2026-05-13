"""UIHandlers — UI-state RPC: locale, CSS injection, config status, game lifecycle.

OP-25h | py_modules/unifideck/rpc/handlers/ui_handlers.py

Mixed-concern handler group covering everything the frontend
needs that doesn't fit cleanly into store / download / launch:

* **Game lifecycle pass-through** — the frontend can notify
  the bus that a game was launched / stopped (used by Steam's
  own "Play" button when the user bypasses Unifideck's launch
  flow).
* **Steam UI manipulation via CDP** — hide / unhide the "Play"
  section for a given AppID, inject custom CSS by marker.
* **Per-game metadata** — enrich a game from the metadata
  service (cached lookups).
* **UI preferences** — read/write the locale.
* **Cloud-failure behaviour per store** — silent vs toast,
  with strict validation of inputs.
* **Config-validation status** — surface the result of the
  startup config-validation pass to the frontend.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from unifideck.core.types import Events
from unifideck.rpc.handlers.base import RpcHandlerBase
from unifideck.rpc.wrapper import RpcError


class UIHandlers(RpcHandlerBase):
    """Mixed-concern UI-side RPC surface."""

    _CLOUD_FAILURE_STORES: ClassVar[tuple[str, ...]] = (
        "default",
        "epic",
        "gog",
        "amazon",
        "ubisoft",
    )
    _CLOUD_FAILURE_MODES: ClassVar[tuple[str, ...]] = ("silent", "toast")
    config_validation_result: Any = None

    async def notify_game_launched(self, store: str, game_id: str, **kw: Any) -> Any:
        """Bridge a frontend-initiated launch onto the bus.

        When the user clicks Steam's own "Play" button on a
        Unifideck shortcut, the dispatcher CLI runs the
        game outside of ``LauncherService``. The frontend
        listens for this transition and calls this method
        so the bus's launch lifecycle events still fire —
        playtime tracking, cloud-save sync etc. all
        observe ``GAME_LAUNCHED`` regardless of who
        initiated the launch.

        Args:
            store: store identifier.
            game_id: store-specific game id.
            **kw: extra payload forwarded as event kwargs.

        Returns:
            ``{success: True}``.
        """
        await self._bus.emit(
            Events.GAME_LAUNCHED,
            store=store,
            game_id=game_id,
            **kw,
        )
        return {"success": True}

    async def notify_game_stopped(
        self,
        store: str,
        game_id: str,
        exit_code: int = 0,
    ) -> Any:
        """Bridge a frontend-detected game exit onto the bus.

        Counterpart to ``notify_game_launched``. The exit
        code defaults to 0 — the frontend may not know the
        real code if it's only watching for window-closed
        signals.

        Args:
            store: store identifier.
            game_id: store-specific game id.
            exit_code: detected exit status (0 if unknown).

        Returns:
            ``{success: True}``.
        """
        await self._bus.emit(
            Events.GAME_STOPPED,
            store=store,
            game_id=game_id,
            exit_code=exit_code,
        )
        return {"success": True}

    async def hide_play_section(self, app_id: int) -> Any:
        """Hide the "Play" section of a Steam game tile via CDP injection.

        Used for games where Unifideck wants to replace
        Steam's native "Play" button with its own (e.g. to
        force the launch to go through ``LauncherService``).

        Args:
            app_id: Steam AppID.

        Returns:
            ``{ok: bool}`` reporting whether the injection
            succeeded.
        """
        from unifideck.cdp.cdp_inject import get_cdp_client

        injector = await get_cdp_client()
        return {"ok": await injector.hide_play_section(app_id)}

    async def unhide_play_section(self, app_id: int) -> Any:
        """Restore Steam's native "Play" section after a hide.

        Inverse of ``hide_play_section``. Used during
        teardown or when a game is uninstalled and the
        override is no longer needed.

        Args:
            app_id: Steam AppID.

        Returns:
            ``{ok: bool}``.
        """
        from unifideck.cdp.cdp_inject import get_cdp_client

        injector = await get_cdp_client()
        return {"ok": await injector.show_play_section(app_id)}

    async def inject_hide_css(self, app_id: int, css: str) -> Any:
        """Inject custom CSS for an app, keyed by a hide-marker.

        The marker (``hide-<app_id>``) lets later calls
        update the same CSS rule rather than appending — the
        injector deduplicates by marker.

        Args:
            app_id: Steam AppID (used to derive the marker).
            css: raw CSS string.

        Returns:
            ``{ok: bool}``.
        """
        from unifideck.cdp.cdp_inject import get_cdp_client

        injector = await get_cdp_client()
        marker = f"hide-{app_id}"
        return {"ok": await injector.inject_css(css, marker)}

    async def get_game_metadata(self, store: str, game_id: str) -> Any:
        """Return enriched metadata for one game.

        Two-step lookup:

        1. Find the game in the synced library (linear scan
           per store — typically <100 entries, fast).
        2. Call the metadata service's enrich method which
           caches Steam / SteamGridDB results.

        Returns ``{}`` if the game isn't in the synced
        library at all.

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            Metadata dict from the metadata service, or
            empty dict when the game is unknown.
        """
        metadata = self._require(self._services.metadata, "metadata")
        for game in self._sync.get_games_by_store(store):
            if game.store_game_id == game_id:
                return cast(dict, await metadata.enrich(game))
        return {}

    async def get_language_preference(self) -> Any:
        """Return the currently-configured UI locale.

        Reads from ``ui.locale`` in the config. Frontend
        uses this on boot to initialise its i18n bundle.

        Returns:
            ``{locale: <str>}``.
        """
        return {"locale": self._config.get("ui.locale")}

    async def set_language_preference(self, locale: str) -> Any:
        """Persist a new UI locale to the config.

        No validation against a list of known locales —
        the frontend is the canonical authority on what
        locales exist, and the backend just stores the
        string. An unknown locale falls back to English on
        the frontend side.

        Args:
            locale: locale code (e.g. ``"en-US"``,
                ``"fr-FR"``).

        Returns:
            ``{success: True, locale}``.
        """
        self._config.set("ui.locale", locale)
        return {"success": True, "locale": locale}

    async def get_cloud_failure_behaviors(self) -> Any:
        """Return the per-store cloud-failure behaviour map.

        Default behaviour is ``"toast"`` (show a user-
        facing notification) for stores that haven't been
        explicitly configured. The ``"default"`` entry is
        used as a fallback for stores not in the explicit
        list.

        Returns:
            ``{store_id → "silent" | "toast"}`` for every
            store in ``_CLOUD_FAILURE_STORES``.
        """
        return {
            store: self._config.get_str(
                f"cloud.failure_behavior.{store}",
                "toast",
            )
            for store in self._CLOUD_FAILURE_STORES
        }

    async def set_cloud_failure_behavior(self, store: str, value: str) -> Any:
        """Persist a cloud-failure behaviour override for one store.

        Strict validation on both fields — unsupported
        values raise typed errors with the allowed list in
        the context dict so the frontend can render a
        clear error message.

        Args:
            store: store id (must be in
                ``_CLOUD_FAILURE_STORES``).
            value: behaviour (must be in
                ``_CLOUD_FAILURE_MODES``).

        Returns:
            ``{success: True, store, value}``.

        Raises:
            RpcError: ``unsupported_store`` or
                ``invalid_behavior`` on bad inputs.
        """
        if store not in self._CLOUD_FAILURE_STORES:
            raise RpcError(
                "unsupported_store",
                store=store,
                supported=list(self._CLOUD_FAILURE_STORES),
            )
        if value not in self._CLOUD_FAILURE_MODES:
            raise RpcError(
                "invalid_behavior",
                value=value,
                supported=list(self._CLOUD_FAILURE_MODES),
            )
        self._config.set(
            f"cloud.failure_behavior.{store}",
            value,
        )
        return {"success": True, "store": store, "value": value}

    async def get_config_validation_status(self) -> Any:
        """Return the result of the startup config-validation pass.

        Three states are possible:

        * No result captured yet (the plugin started in
          a stripped-down mode without validation) →
          report a healthy default (all True / 0 errors).
        * Successful validation → ``degraded=False`` with
          counts and flags.
        * Failed validation → ``degraded=True`` with the
          first 20 errors (path + source + message).

        The 20-error cap prevents bloated payloads when
        many keys fail simultaneously.

        Returns:
            Validation status dict for the frontend's
            health-check banner.
        """
        result = self.config_validation_result
        if result is None:
            return {
                "degraded": False,
                "defaults_validated": True,
                "user_overrides_present": False,
                "error_count": 0,
                "errors": [],
            }
        return {
            "degraded": not result.success,
            "defaults_validated": result.defaults_validated,
            "user_overrides_present": result.user_overrides_present,
            "error_count": len(result.errors),
            "errors": [
                {"source": e.source, "path": e.path, "message": e.message}
                for e in result.errors[:20]
            ],
        }
