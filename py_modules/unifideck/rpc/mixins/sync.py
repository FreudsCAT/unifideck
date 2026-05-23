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

    async def sync_libraries(
        self, fetch_artwork: bool = True, **kw: Any,
    ) -> Any:
        """Trigger a full library sync across every store.

        Args:
            fetch_artwork: when ``False``, skip the artwork
                download phase entirely. Used by background /
                scheduled syncs that only need a fresh game list.

        The underlying service method is ``sync_all`` (an earlier
        version called ``sync`` which doesn't exist on
        :class:`SyncService` — the RPC raised ``AttributeError``).
        """
        return await self.sync_service.sync_all(
            fetch_artwork=fetch_artwork, **kw,
        )

    async def force_sync_libraries(
        self, resync_artwork: bool = False, **kw: Any,
    ) -> Any:
        """Like ``sync_libraries`` but bypasses per-store cache TTLs.

        Used for "force refresh" — when the cache hasn't
        expired but the library is known to have changed.

        Args:
            resync_artwork: when ``True``, ArtworkService clears
                its SGDB failure-cooldown cache and bypasses the
                ``has_artwork`` on-disk skip so every game gets a
                fresh download. Wired end-to-end via the
                SYNC_COMPLETE event payload.
            **kw: forwarded with ``force=True`` added.

        Returns:
            Sync-outcome dict.
        """
        return await self.sync_service.sync_all(
            force=True, resync_artwork=resync_artwork, **kw,
        )

    async def get_sync_status(self) -> Any:
        """Return whether a sync is running + last completion time."""
        return self.sync_service.get_status()

    async def get_sync_progress(self) -> Any:
        """Return per-store progress during an in-flight sync.

        Progress is bundled into ``get_status`` — there is no
        separate ``get_progress`` on :class:`SyncService`.
        """
        return self.sync_service.get_status()

    async def refresh_store(self, store_name: str) -> Any:
        """Sync a single store — used by the per-store refresh button.

        Calls ``sync_single_store`` on ``SyncService`` (no single-flight
        lock — the caller is responsible for not racing a full sync).
        Returns ``{"success": bool, "error": str | None}`` so the
        frontend can show a brief toast for each refresh.
        """
        ok, err = await self.sync_service.sync_single_store(store_name)
        return {"success": ok, "error": err}

    async def cancel_sync(self) -> Any:
        """Cancel an in-flight sync."""
        return await self.sync_service.cancel()

    async def request_auth_sync(self, store: str) -> Any:
        """Frontend-callable trigger for post-login refresh.

        Called by AuthDispatcher after a store login completes
        (e.g. Ubisoft, which has no browser-callback auth). If a
        sync is already running, the request is queued behind it
        via ``SyncService._enqueue``; the response carries
        ``restart_pending=True`` so the frontend knows to re-listen
        for ``SYNC_STARTED``.
        """
        return await self.sync_service.request_auth_sync(store)

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
    registry: Any

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

    @staticmethod
    def _collect_unifideck_app_ids(shortcut_svc: Any) -> list[int]:
        """Union of app_ids from shortcuts.vdf (tagged) and games.map.

        Pulls the canonical Unifideck-owned app_id set from the
        persisted shortcut state — not the volatile sync cache —
        so cleanup works even when no sync has run this session.
        """
        from unifideck.services.shortcut.games_map import UNIFIDECK_TAG

        ids: set[int] = set()
        shortcuts = getattr(shortcut_svc, "_shortcuts", None) or {}
        root = shortcuts.get("shortcuts") if isinstance(shortcuts, dict) else None
        if isinstance(root, dict):
            for entry in root.values():
                if not isinstance(entry, dict):
                    continue
                tags = entry.get("tags")
                tag_values: list[Any] = []
                if isinstance(tags, dict):
                    tag_values = list(tags.values())
                elif isinstance(tags, list):
                    tag_values = list(tags)
                if not any(
                    isinstance(v, str) and v == UNIFIDECK_TAG
                    for v in tag_values
                ):
                    continue
                app_id = entry.get("appid")
                if isinstance(app_id, int):
                    ids.add(app_id)

        games_map = getattr(shortcut_svc, "_games_map", None) or {}
        if isinstance(games_map, dict):
            for entry in games_map.values():
                app_id = getattr(entry, "app_id", 0)
                if isinstance(app_id, int) and app_id != 0:
                    ids.add(app_id)

        return sorted(ids)

    async def _cleanup_one_app_id(
        self,
        app_id: int,
        delete_files: bool,
        shortcut_svc: Any,
        home: str,
        safe_roots: tuple[str, ...],
    ) -> tuple[int | None, bool]:
        """Per-app_id cleanup: returns (removed_app_id_or_None, files_deleted)."""
        install_dir: str | None = None
        if delete_files:
            games_map = getattr(shortcut_svc, "_games_map", None) or {}
            if isinstance(games_map, dict):
                for entry in games_map.values():
                    if getattr(entry, "app_id", 0) == app_id:
                        install_dir = getattr(entry, "work_dir", None)
                        break

        removed_id: int | None = None
        try:
            if await shortcut_svc.remove_game(app_id):
                removed_id = app_id
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

    async def _delete_artwork_for_app_ids(self, app_ids: list[int]) -> int:
        """Delete Steam grid artwork files for each given app_id.

        Files are written by :mod:`unifideck.services.artwork.fetcher`
        as ``<grid_dir>/<unsigned><suffix>``. Every Unifideck app_id
        has bit ``0x80000000`` set, so the unsigned form is always
        ≥ 2³¹ — no collision with real Steam appids living in the
        same dir.
        """
        import asyncio
        from pathlib import Path

        if not app_ids:
            return 0
        artwork = getattr(self.services, "artwork", None)
        grid_dir = getattr(artwork, "grid_dir", None) if artwork else None
        if not grid_dir:
            logger.warning(
                "[cleanup] artwork service unavailable; skipping grid wipe",
            )
            return 0

        def _sweep() -> int:
            base = Path(grid_dir)
            if not base.is_dir():
                return 0
            count = 0
            for app_id in app_ids:
                unsigned = app_id if app_id >= 0 else app_id + 0x100000000
                for match in base.glob(f"{unsigned}*"):
                    if not match.is_file():
                        continue
                    try:
                        match.unlink(missing_ok=True)
                        count += 1
                    except OSError:
                        logger.exception(
                            "[cleanup] unlink(%s) failed", match,
                        )
            return count

        return await asyncio.to_thread(_sweep)

    async def _logout_all_stores(self) -> int:
        """Sign out of every store via the registry's logout flow.

        Reuses :meth:`StoreRegistry.logout_all` — the same code path
        the per-store "Sign out" buttons use. Returns the count of
        stores that reported a successful logout.
        """
        registry = getattr(self, "registry", None)
        if registry is None:
            return 0
        try:
            results = await registry.logout_all()
        except Exception:
            logger.exception("[cleanup] registry.logout_all failed")
            return 0
        if not isinstance(results, dict):
            return 0
        return sum(1 for ok in results.values() if ok)

    async def _delete_stray_auth_files(self) -> int:
        """Delete leftover auth-URL temp files under unifideck data dir."""
        import asyncio
        from pathlib import Path

        candidates = (
            "~/.local/share/unifideck/gog_auth_url.txt",
            "~/.local/share/unifideck/ms_auth_url.txt",
            "~/.local/share/unifideck/epic_auth_url.txt",
            "~/.local/share/unifideck/amazon_auth_url.txt",
            "~/.local/share/unifideck/ubisoft_upc_session.txt",
        )

        def _sweep() -> int:
            count = 0
            for raw in candidates:
                p = Path(raw).expanduser()
                try:
                    if p.is_file():
                        p.unlink(missing_ok=True)
                        count += 1
                except OSError:
                    logger.exception(
                        "[cleanup] unlink(%s) failed", p,
                    )
            return count

        return await asyncio.to_thread(_sweep)

    async def perform_full_cleanup(
        self, delete_files: bool = False,
    ) -> dict[str, Any]:
        """Wipe every Unifideck-managed shortcut, artwork, auth, and cache.

        Pulls the Unifideck app_id set from the persisted shortcut
        state (``shortcuts.vdf`` + ``games.map``) — not the volatile
        sync cache — so cleanup works even when no sync has run in
        the current process.

        Steps:

        1. Load shortcut state from disk if not already loaded.
        2. Build the candidate app_id set from tagged VDF entries
           and stored ``games.map`` app_ids.
        3. For each app_id: remove via ``shortcut.remove_game``
           (drops both VDF + games.map rows, emits ``SHORTCUT_REMOVED``).
           If ``delete_files=True`` and the install dir sits under a
           safe root, rm -rf it.
        4. Delete Steam grid artwork files keyed to the deleted app_ids.
        5. Sign out of every store via ``registry.logout_all``.
        6. Sweep stray auth-URL temp files.
        7. Clear every registered cache namespace.

        Returns the result data only (no ``success``/``error`` keys);
        the RPC wrapper adds the envelope. On failure, raises
        ``RuntimeError`` and the wrapper converts it to a typed
        ``internal_error`` envelope.
        """
        import asyncio
        from pathlib import Path

        logger.info(
            "[cleanup] starting (delete_files=%s)", delete_files,
        )
        deleted_app_ids: list[int] = []
        deleted_files_count = 0
        shortcut_svc = getattr(self.services, "shortcut", None)
        if shortcut_svc is None:
            raise RuntimeError("shortcut service unavailable")

        await shortcut_svc._load_shortcuts()
        await shortcut_svc._load_games_map()

        app_ids = self._collect_unifideck_app_ids(shortcut_svc)
        logger.info("[cleanup] %d candidate app_ids", len(app_ids))
        safe_roots = ("/Games/", "/Epic", "/GOG", "/Amazon", "unifideck")
        home = await asyncio.to_thread(
            lambda: str(Path("~").expanduser()),
        )
        for app_id in app_ids:
            removed_id, files_deleted = await self._cleanup_one_app_id(
                app_id, delete_files, shortcut_svc, home, safe_roots,
            )
            if removed_id is not None:
                deleted_app_ids.append(removed_id)
            if files_deleted:
                deleted_files_count += 1
        logger.info(
            "[cleanup] removed %d shortcuts, %d install dirs",
            len(deleted_app_ids), deleted_files_count,
        )

        deleted_artwork_count = await self._delete_artwork_for_app_ids(
            deleted_app_ids or app_ids,
        )
        logged_out_count = await self._logout_all_stores()
        deleted_stray_files_count = await self._delete_stray_auth_files()
        logger.info(
            "[cleanup] artwork=%d logged_out=%d stray=%d",
            deleted_artwork_count, logged_out_count, deleted_stray_files_count,
        )

        for name in list(getattr(self.cache, "_stores", {}).keys()):
            try:
                self.cache.clear(name)
            except Exception:
                logger.exception(
                    "[cleanup] cache.clear(%s) failed", name,
                )

        logger.info("[cleanup] complete")
        return {
            "deleted_games": len(deleted_app_ids),
            "deleted_files_count": deleted_files_count,
            "deleted_artwork_count": deleted_artwork_count,
            "logged_out_count": logged_out_count,
            "deleted_stray_files_count": deleted_stray_files_count,
            "deleted_app_ids": deleted_app_ids,
        }
