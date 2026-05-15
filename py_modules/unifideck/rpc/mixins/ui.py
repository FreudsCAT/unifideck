"""UI RPC mixin for Plugin class.

OP-26g | rpc/mixins/ui.py
"""
from __future__ import annotations

import logging
import os
from typing import Any

from unifideck.rpc.errors import RpcError

logger = logging.getLogger(__name__)


class UIRPCMixin:
    """CDP injection, game metadata, and language preferences."""

    config: Any
    services: Any
    sync_service: Any  # Required for the metadata.enrich(game) lookup

    async def get_game_metadata(self, store: str, game_id: str) -> Any:
        """Return merged metadata for a game from the sync cache.

        :class:`MetadataService` does not expose ``get(store, id)``
        — its real public method is :meth:`enrich(game)` which
        takes a ``Game`` object. We resolve the game via the sync
        cache then enrich. An earlier version called
        ``metadata.get(...)`` and the RPC always raised
        ``AttributeError``.
        """
        metadata = getattr(self.services, "metadata", None)
        if metadata is None:
            raise RpcError("service_unavailable", service="metadata")
        sync = getattr(self, "sync_service", None)
        if sync is None:
            raise RpcError("service_unavailable", service="sync_service")
        for game in sync.get_all_games():
            # ``game_id`` here is the store-native id (the RPC
            # argument name predates the rename to
            # ``store_game_id`` on the dataclass).
            if game.store == store and game.store_game_id == game_id:
                return await metadata.enrich(game)
        return {}

    async def hide_play_section(self, app_id: int) -> Any:
        """Inject CSS hiding a game's Play button in Steam UI.

        Routes through the :class:`SteamCSSInjector` singleton
        (see :mod:`unifideck.cdp.cdp_inject`) — ``self.services.cdp``
        is the low-level ``CDPClient`` and has no
        ``hide_play_section`` method, so the previous version
        raised ``AttributeError`` on every "Hide" button click.
        """
        from unifideck.cdp import get_cdp_client
        injector = await get_cdp_client()
        return await injector.hide_play_section(app_id)

    async def unhide_play_section(self, app_id: int) -> Any:
        """Remove the hide-play-section CSS injection.

        Real method on the injector is :meth:`show_play_section`
        (matching the inject/show pair). Previous ``unhide_*``
        call didn't exist on either CDPClient or SteamCSSInjector.
        """
        from unifideck.cdp import get_cdp_client
        injector = await get_cdp_client()
        return await injector.show_play_section(app_id)

    async def inject_hide_css(self, app_id: int, css: str) -> Any:
        """Inject arbitrary CSS keyed by app_id.

        :meth:`SteamCSSInjector.inject_css` takes
        ``(css, marker)``. An earlier version passed
        ``(app_id, css)`` so the CSS string was discarded and
        ``app_id`` was treated as the CSS source.
        """
        from unifideck.cdp import get_cdp_client
        from unifideck.cdp.cdp_inject import build_marker_id
        injector = await get_cdp_client()
        marker = build_marker_id(f"app_{app_id}")
        return await injector.inject_css(css, marker)

    async def get_language_preference(self) -> Any:
        """Return the current UI locale config value."""
        return {"locale": self.config.get("ui.locale", "en-US")}

    async def set_language_preference(self, locale: str) -> Any:
        """Persist the UI locale via config."""
        self.config.set("ui.locale", locale)
        return {"success": True, "locale": locale}

    async def list_directory(
        self,
        path: str,
        show_hidden: bool = False,
        sort_by: str = "name",
    ) -> Any:
        """Enumerate immediate subdirectories of ``path``.

        Backs the frontend ``StoragePathPicker`` which
        navigates step-by-step (one ``list_directory`` per
        click) so we never have to ship a tree of the whole
        filesystem.

        Args:
            path: absolute path to enumerate. ``~`` is
                expanded.
            show_hidden: include dotfile entries.
            sort_by: ``"name"`` (only sort supported today).

        Returns:
            ``{success, path, directories: [str]}``. On any
            OS-level error the response is non-success with
            an ``error`` field — callers don't need to
            handle exceptions.
        """
        try:
            resolved = os.path.realpath(os.path.expanduser(path or "/"))
            if not os.path.isdir(resolved):
                return {
                    "success": False,
                    "error": "not_a_directory",
                    "path": resolved,
                    "directories": [],
                }
            entries: list[str] = []
            with os.scandir(resolved) as it:
                for entry in it:
                    if not show_hidden and entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            entries.append(entry.name)
                    except OSError:
                        continue
            if sort_by == "name":
                entries.sort(key=str.lower)
            return {
                "success": True,
                "path": resolved,
                "directories": entries,
            }
        except PermissionError as e:
            return {
                "success": False,
                "error": "permission_denied",
                "path": path,
                "directories": [],
                "detail": str(e),
            }
        except OSError as e:
            return {
                "success": False,
                "error": "os_error",
                "path": path,
                "directories": [],
                "detail": str(e),
            }
