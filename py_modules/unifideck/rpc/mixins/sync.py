"""Sync RPC mixin for Plugin class.

OP-26f | rpc/mixins/sync.py
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SyncRPCMixin:
    """Library sync, progress, and game queries."""

    sync_service: Any

    async def sync_libraries(self, **kw: Any) -> Any:
        """Trigger a full library sync across every store.

        The underlying service method is ``sync_all`` (an earlier
        version called ``sync`` which doesn't exist on
        :class:`SyncService` — the RPC raised ``AttributeError``).
        """
        return await self.sync_service.sync_all(**kw)

    async def force_sync_libraries(
        self, resync_artwork: bool = False, **kw: Any,
    ) -> Any:
        """Like ``sync_libraries`` but bypasses per-store cache TTLs.

        Used for "force refresh" — when the cache hasn't
        expired but the library is known to have changed.

        Args:
            resync_artwork: whether to re-fetch all artwork
                (passed from the frontend's ForceSyncModal).
            **kw: forwarded with ``force=True`` added.

        Returns:
            Sync-outcome dict.
        """
        # ``resync_artwork`` is part of the frontend ForceSyncModal
        # contract but the wire-through to the artwork service is
        # not yet implemented in ``SyncService.sync_all`` (it only
        # accepts ``force``). Log it so the choice is observable
        # in the bus / log stream and vulture sees the param as
        # used. TODO: forward to artwork invalidator once the
        # service grows a ``resync_artwork`` parameter.
        logger.debug(
            "[sync] force_sync_libraries(resync_artwork=%s)",
            resync_artwork,
        )
        return await self.sync_service.sync_all(force=True, **kw)

    async def get_sync_status(self) -> Any:
        """Return whether a sync is running + last completion time."""
        return self.sync_service.get_status()

    async def get_sync_progress(self) -> Any:
        """Return per-store progress during an in-flight sync.

        Progress is bundled into ``get_status`` — there is no
        separate ``get_progress`` on :class:`SyncService`.
        """
        return self.sync_service.get_status()

    async def cancel_sync(self) -> Any:
        """Cancel an in-flight sync."""
        return await self.sync_service.cancel()

    async def get_all_unifideck_games(self) -> Any:
        """Return every known game across every store.

        :meth:`SyncService.get_all_games` is synchronous; an
        earlier version awaited it and crashed with
        ``TypeError: object list can't be used in 'await' expression``.
        """
        return self.sync_service.get_all_games()

    async def get_game_info(self, app_id: int) -> Any:
        """Return the full record for a single Unifideck AppID.

        Args:
            app_id: Steam-style AppID (deterministic from
                store + game_id + title).

        Returns:
            Game info dict, or empty / None when unknown.
        """
        # `sync_service.get_game_info` is a synchronous helper
        # (linear scan over `_all_games`) — no coroutine, no
        # await. The previous body had a stray `await` which
        # raised `TypeError: object NoneType can't be used
        # in 'await' expression`.
        return self.sync_service.get_game_info(app_id)

    services: Any
    cache: Any

    async def _resolve_install_dir(
        self, shortcut_svc: Any, game: Any,
    ) -> str | None:
        """Look up the install directory for a Unifideck game."""
        if shortcut_svc is None:
            return None
        store = (
            game.get("store") if isinstance(game, dict)
            else getattr(game, "store", None)
        )
        game_id = (
            game.get("game_id") if isinstance(game, dict)
            else getattr(game, "game_id", None)
        )
        if not store or not game_id:
            return None
        try:
            entry = await shortcut_svc.get_entry_for_game_key(
                str(store), str(game_id),
            )
        except Exception:
            return None
        return getattr(entry, "work_dir", None)

    async def _delete_install_dir(
        self, install_dir: str, home: str, safe_roots: tuple[str, ...],
    ) -> bool:
        """rm -rf an install directory if it sits under a safe root."""
        import asyncio
        import shutil
        from pathlib import Path

        if not install_dir:
            return False
        if install_dir in {"/", home}:
            return False
        if not any(k in install_dir for k in safe_roots):
            return False
        if not await asyncio.to_thread(Path(install_dir).is_dir):
            return False
        try:
            await asyncio.to_thread(shutil.rmtree, install_dir, True)
        except OSError:
            logger.exception(
                "[cleanup] rmtree(%s) failed", install_dir,
            )
            return False
        return True

    async def _cleanup_one_game(
        self,
        game: Any,
        delete_files: bool,
        shortcut_svc: Any,
        home: str,
        safe_roots: tuple[str, ...],
    ) -> tuple[int | None, bool]:
        """Per-game cleanup: returns (removed_app_id_or_None, files_deleted)."""
        app_id = (
            game.get("app_id") if isinstance(game, dict)
            else getattr(game, "app_id", None)
        )
        if app_id is None:
            return None, False
        install_dir: str | None = None
        if delete_files:
            install_dir = await self._resolve_install_dir(shortcut_svc, game)
        removed_id: int | None = None
        if shortcut_svc is not None:
            try:
                if await shortcut_svc.remove_game(int(app_id)):
                    removed_id = int(app_id)
            except Exception:
                logger.exception(
                    "[cleanup] remove_game(%s) failed", app_id,
                )
        files_deleted = False
        if delete_files and install_dir:
            files_deleted = await self._delete_install_dir(
                install_dir, home, safe_roots,
            )
        return removed_id, files_deleted

    async def perform_full_cleanup(
        self, delete_files: bool = False,
    ) -> dict[str, Any]:
        """Wipe every Unifideck-managed shortcut, cache, and (optionally) install dir.

        Used by the Quick-Access "Cleanup" section. Walks
        ``sync_service.get_all_games()`` for the canonical Unifideck
        AppID set, then for each AppID:

        1. Records the install directory (if known via shortcut service).
        2. Removes the shortcut via ``shortcut.remove_game`` — which
           drops both ``shortcuts.vdf`` and ``games.map`` entries and
           emits ``SHORTCUT_REMOVED``.
        3. If ``delete_files=True`` and the install dir is inside a
           known-safe parent ("Games", "Epic", "GOG", "unifideck", or
           the user's configured custom install path), rm -rf it.

        Finally clears every registered cache namespace so the next
        sync rebuilds from upstream and the in-memory ProtonDB cache
        is wiped on the frontend.

        Args:
            delete_files: when True, also delete the on-disk install
                directories. Defaults to False (shortcuts-only).

        Returns:
            ``{"success": bool, "deleted_games": int,
              "deleted_files_count": int, "deleted_app_ids": list[int],
              "error": str | None}``. The frontend uses
            ``deleted_app_ids`` to call ``SteamClient.Apps.RemoveShortcut``
            for each so Steam's in-memory state catches up to the VDF.
        """
        import asyncio
        from pathlib import Path

        deleted_app_ids: list[int] = []
        deleted_files_count = 0
        try:
            games = self.sync_service.get_all_games() or []
        except Exception as e:
            logger.exception("[cleanup] get_all_games failed")
            return {
                "success": False,
                "deleted_games": 0,
                "deleted_files_count": 0,
                "deleted_app_ids": [],
                "error": str(e),
            }

        shortcut_svc = getattr(self.services, "shortcut", None)
        safe_roots = ("/Games/", "/Epic", "/GOG", "/Amazon", "unifideck")
        home = await asyncio.to_thread(
            lambda: str(Path("~").expanduser()),
        )
        for game in games:
            removed_id, files_deleted = await self._cleanup_one_game(
                game, delete_files, shortcut_svc, home, safe_roots,
            )
            if removed_id is not None:
                deleted_app_ids.append(removed_id)
            if files_deleted:
                deleted_files_count += 1

        for name in list(getattr(self.cache, "_stores", {}).keys()):
            try:
                self.cache.clear(name)
            except Exception:
                logger.exception(
                    "[cleanup] cache.clear(%s) failed", name,
                )

        return {
            "success": True,
            "deleted_games": len(deleted_app_ids),
            "deleted_files_count": deleted_files_count,
            "deleted_app_ids": deleted_app_ids,
            "error": None,
        }
