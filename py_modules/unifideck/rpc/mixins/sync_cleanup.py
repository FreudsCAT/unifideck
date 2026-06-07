"""Full-cleanup RPC machinery for :class:`SyncRPCMixin`.

OP-26f-bis | rpc/mixins/sync_cleanup.py

Extracted from ``rpc/mixins/sync.py`` to keep that file under the
550-LOC volumetry cap. Holds the "Delete all Unifideck data" flow —
shortcut removal, grid-artwork wipe, store sign-out, credential
deletion, and cache clearing — plus the small static helpers that
collect the Unifideck-owned app_id set from the persisted shortcut
state. ``SyncRPCMixin`` mixes this in, so the public RPC surface
(``perform_full_cleanup``) is unchanged.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def _is_unifideck_owned(
    entry: dict[str, Any],
    unifideck_tag: str,
    is_unifideck_launch_options: Callable[[str], bool],
) -> bool:
    """True iff a VDF shortcut entry is Unifideck-owned.

    Two independent signals so cleanup catches entries even when
    Steam silently strips one of them:

    * **LaunchOptions pattern** — most reliable, Steam preserves
      ``LaunchOptions`` across updates.
    * **UNIFIDECK_TAG** in ``tags`` — secondary signal for old
      entries that pre-date the LaunchOptions convention.
    """
    launch = entry.get("LaunchOptions", "")
    if isinstance(launch, str) and is_unifideck_launch_options(launch):
        return True
    tags = entry.get("tags")
    tag_values: list[Any] = []
    if isinstance(tags, dict):
        tag_values = list(tags.values())
    elif isinstance(tags, list):
        tag_values = list(tags)
    return any(
        isinstance(v, str) and v == unifideck_tag for v in tag_values
    )


def _sweep_nonsteam_grid(grid_dir: str, keep_appids: set[int]) -> int:
    """Delete non-Steam grid artwork files not in *keep_appids*.

    Files are named ``<grid_dir>/<unsigned><suffix>``; real Steam
    appids are < 2³¹, so any ``>= 0x80000000`` prefix is a non-Steam
    shortcut's art. Blocking I/O — call from a thread.
    """
    import re
    from pathlib import Path

    prefix_re = re.compile(r"^(\d+)")
    base = Path(grid_dir)
    if not base.is_dir():
        return 0
    count = 0
    for match in base.iterdir():
        if not match.is_file():
            continue
        m = prefix_re.match(match.name)
        if not m:
            continue
        appid = int(m.group(1))
        if appid < 0x80000000 or appid in keep_appids:
            continue
        try:
            match.unlink(missing_ok=True)
            count += 1
        except OSError:
            logger.exception("[cleanup] unlink(%s) failed", match)
    return count


class CleanupRPCMixin:
    """"Delete all Unifideck data" flow + its app_id collectors."""

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
            logger.exception("[cleanup] rmtree(%s) failed", install_dir)
            return False
        return True

    @staticmethod
    def _collect_unifideck_app_ids(shortcut_svc: Any) -> list[int]:
        """Union of app_ids from shortcuts.vdf (tagged) and games.map.

        Pulls the canonical Unifideck-owned app_id set from the
        persisted shortcut state — not the volatile sync cache —
        so cleanup works even when no sync has run this session.
        """
        ids: set[int] = set()
        ids.update(CleanupRPCMixin._collect_ids_from_shortcuts_vdf(shortcut_svc))
        ids.update(CleanupRPCMixin._collect_ids_from_games_map(shortcut_svc))
        return sorted(ids)

    @staticmethod
    def _collect_ids_from_shortcuts_vdf(shortcut_svc: Any) -> set[int]:
        """Walk ``shortcuts.vdf`` and return appids of Unifideck entries."""
        from unifideck.services.shortcut.games_map import UNIFIDECK_TAG
        from unifideck.services.shortcut.launch_options import is_unifideck_shortcut

        ids: set[int] = set()
        shortcuts = getattr(shortcut_svc, "_shortcuts", None) or {}
        root = shortcuts.get("shortcuts") if isinstance(shortcuts, dict) else None
        if not isinstance(root, dict):
            return ids
        for entry in root.values():
            if not isinstance(entry, dict):
                continue
            if not _is_unifideck_owned(entry, UNIFIDECK_TAG, is_unifideck_shortcut):
                continue
            app_id = entry.get("appid")
            if isinstance(app_id, int):
                ids.add(app_id)
        return ids

    @staticmethod
    def _collect_ids_from_games_map(shortcut_svc: Any) -> set[int]:
        """Pull non-zero appids out of the games.map manifest."""
        ids: set[int] = set()
        games_map = getattr(shortcut_svc, "_games_map", None) or {}
        if not isinstance(games_map, dict):
            return ids
        for entry in games_map.values():
            app_id = getattr(entry, "app_id", 0)
            if isinstance(app_id, int) and app_id != 0:
                ids.add(app_id)
        return ids

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
            logger.exception("[cleanup] remove_game(%s) failed", app_id)

        files_deleted = False
        if delete_files and install_dir:
            files_deleted = await self._delete_install_dir(
                install_dir, home, safe_roots,
            )
        return removed_id, files_deleted

    @staticmethod
    def _nonunifideck_unsigned_appids(shortcut_svc: Any) -> set[int]:
        """Unsigned appids of currently-present *non-Unifideck* shortcuts.

        These back other launchers' shortcuts (Heroic, manually-added
        apps, …) that happen to live in the same ``grid/`` dir. They are
        the wipe's protected set — everything else with a non-Steam
        (≥ 2³¹) appid is fair game.
        """
        from unifideck.services.shortcut.games_map import UNIFIDECK_TAG
        from unifideck.services.shortcut.launch_options import (
            is_unifideck_shortcut,
        )

        keep: set[int] = set()
        shortcuts = getattr(shortcut_svc, "_shortcuts", None) or {}
        root = shortcuts.get("shortcuts") if isinstance(shortcuts, dict) else None
        if not isinstance(root, dict):
            return keep
        for entry in root.values():
            if not isinstance(entry, dict):
                continue
            if _is_unifideck_owned(entry, UNIFIDECK_TAG, is_unifideck_shortcut):
                continue
            app_id = entry.get("appid")
            if isinstance(app_id, int):
                keep.add(app_id if app_id >= 0 else app_id + 0x100000000)
        return keep

    async def _delete_nonsteam_artwork(self, keep_appids: set[int]) -> int:
        """Wipe every non-Steam grid artwork file, except ``keep_appids``.

        "Delete all Unifideck data" should clear artwork outright —
        including art orphaned by past removals that no longer maps to
        any shortcut — so other launchers' live art (``keep_appids``)
        survives but everything else non-Steam is removed.
        """
        import asyncio

        artwork = getattr(self.services, "artwork", None)
        grid_dir = getattr(artwork, "grid_dir", None) if artwork else None
        if not grid_dir:
            logger.warning(
                "[cleanup] artwork service unavailable; skipping grid wipe",
            )
            return 0
        return await asyncio.to_thread(
            _sweep_nonsteam_grid, grid_dir, keep_appids,
        )

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
        # ``logout_all`` maps each store to ``{"success", "error"}`` —
        # count only the entries that actually reported success (a
        # non-empty dict is always truthy, so ``if v`` would over-count).
        return sum(
            1
            for v in results.values()
            if isinstance(v, dict) and v.get("success")
        )

    def _reset_store_availability(self) -> None:
        """Clear the in-memory ``_cached_available`` flag on every store.

        ``check_store_status`` re-probes live, but ``get_store_infos``
        and other surfaces read the cached flag — resetting it makes the
        settings badges reflect signed-out immediately after a wipe,
        without waiting for the next availability probe.
        """
        registry = getattr(self, "registry", None)
        stores = getattr(registry, "_stores", None)
        if not isinstance(stores, dict):
            return
        for store in stores.values():
            try:
                store._cached_available = False
            except Exception:
                logger.exception(
                    "[cleanup] reset _cached_available failed for %s",
                    getattr(store, "store_name", "?"),
                )

    async def _delete_auth_data(self) -> int:
        """Delete every store's persisted auth data + stray temp files.

        Belt-and-suspenders on top of ``registry.logout_all`` — each
        store's ``logout`` *should* clear its own credentials, but it
        no-ops when the auth submodule isn't wired yet and its CLI
        logout swallows timeout/OS errors. Deleting the credential
        files the ``is_available`` probes read guarantees the stores
        report signed-out afterward.
        """
        import asyncio
        from pathlib import Path

        candidates = (
            # Persisted credentials read by each store's ``is_available``.
            "~/.config/legendary/user.json",
            "~/.config/nile/user.json",
            "~/.config/unifideck/gog_token.json",
            "~/.config/unifideck/gogdl/gog_credentials.json",
            "~/.local/share/unifideck/microsoft_tokens.json",
            # Stray auth-URL temp files left mid-flow.
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
                    logger.exception("[cleanup] unlink(%s) failed", p)
            return count

        return await asyncio.to_thread(_sweep)

    async def perform_full_cleanup(
        self, delete_files: bool = False,
    ) -> dict[str, Any]:
        """Wipe every Unifideck-managed shortcut, artwork, auth, and cache.

        Pulls the Unifideck app_id set from the persisted shortcut
        state (``shortcuts.vdf`` + ``games.map``) — not the volatile
        sync cache — so cleanup works even when no sync has run in
        the current process. Removes shortcuts (+ optional install
        dirs under a safe root), wipes orphaned non-Steam grid art,
        signs out of every store, deletes persisted credentials, and
        clears every cache namespace.

        Returns the result data only (no ``success``/``error`` keys);
        the RPC wrapper adds the envelope. On failure, raises
        ``RuntimeError`` and the wrapper converts it to a typed
        ``internal_error`` envelope.
        """
        logger.info("[cleanup] starting (delete_files=%s)", delete_files)
        shortcut_svc = getattr(self.services, "shortcut", None)
        if shortcut_svc is None:
            raise RuntimeError("shortcut service unavailable")

        await shortcut_svc._load_shortcuts()
        await shortcut_svc._load_games_map()

        app_ids = self._collect_unifideck_app_ids(shortcut_svc)
        logger.info("[cleanup] %d candidate app_ids", len(app_ids))
        deleted_app_ids, deleted_files_count = await self._remove_shortcuts(
            shortcut_svc, app_ids, delete_files,
        )
        wiped = await self._wipe_residual_data(shortcut_svc)
        self._clear_all_caches()

        logger.info("[cleanup] complete")
        return {
            "deleted_games": len(deleted_app_ids),
            "deleted_files_count": deleted_files_count,
            "deleted_artwork_count": wiped["artwork"],
            "logged_out_count": wiped["logged_out"],
            "deleted_stray_files_count": wiped["stray"],
            "deleted_app_ids": deleted_app_ids,
        }

    async def _remove_shortcuts(
        self, shortcut_svc: Any, app_ids: list[int], delete_files: bool,
    ) -> tuple[list[int], int]:
        """Remove every candidate shortcut (+ optional install dir).

        Suppresses the per-removal artwork handler for the duration of
        the loop: each ``remove_game`` emits ``SHORTCUT_REMOVED`` (whose
        handlers are awaited), which would otherwise glob-and-delete the
        grid dir once per game. The single ``_delete_nonsteam_artwork``
        sweep clears all of it — current art AND orphans — in one pass.
        """
        import asyncio
        from pathlib import Path

        deleted_app_ids: list[int] = []
        deleted_files_count = 0
        safe_roots = ("/Games/", "/Epic", "/GOG", "/Amazon", "unifideck")
        home = await asyncio.to_thread(lambda: str(Path("~").expanduser()))
        artwork_svc = getattr(self.services, "artwork", None)
        if artwork_svc is not None:
            artwork_svc._suppress_removal_cleanup = True
        try:
            for app_id in app_ids:
                removed_id, files_deleted = await self._cleanup_one_app_id(
                    app_id, delete_files, shortcut_svc, home, safe_roots,
                )
                if removed_id is not None:
                    deleted_app_ids.append(removed_id)
                if files_deleted:
                    deleted_files_count += 1
        finally:
            if artwork_svc is not None:
                artwork_svc._suppress_removal_cleanup = False
        logger.info(
            "[cleanup] removed %d shortcuts, %d install dirs",
            len(deleted_app_ids), deleted_files_count,
        )
        return deleted_app_ids, deleted_files_count

    async def _wipe_residual_data(self, shortcut_svc: Any) -> dict[str, int]:
        """Wipe orphaned artwork, sign out stores, delete auth, reset flags.

        Shortcut removal already dropped our entries from ``_shortcuts``,
        so the artwork keep-set is exactly the foreign shortcuts that
        remain (other launchers' live art).
        """
        keep_appids = self._nonunifideck_unsigned_appids(shortcut_svc)
        artwork = await self._delete_nonsteam_artwork(keep_appids)
        logged_out = await self._logout_all_stores()
        stray = await self._delete_auth_data()
        self._reset_store_availability()
        logger.info(
            "[cleanup] artwork=%d logged_out=%d stray=%d",
            artwork, logged_out, stray,
        )
        return {"artwork": artwork, "logged_out": logged_out, "stray": stray}

    def _clear_all_caches(self) -> None:
        """Clear every registered cache namespace."""
        for name in list(getattr(self.cache, "_stores", {}).keys()):
            try:
                self.cache.clear(name)
            except Exception:
                logger.exception("[cleanup] cache.clear(%s) failed", name)
