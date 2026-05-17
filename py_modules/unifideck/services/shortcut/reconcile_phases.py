"""Reconcile phases mixin — bulk shortcut sync from a library snapshot.

OP-14c-bis | py_modules/unifideck/services/shortcut/reconcile_phases.py

Extracted from ``games_map_mixin.py`` (2026-05-17) to keep the
host file under the 550 LOC volumetry cap. Contains the bulk
reconcile method + its five phase helpers — the set-diff
algorithm that adds missing shortcuts, removes stale ones, and
reclaims orphaned entries by AppID from the persistent registry.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .games_map import GameMapEntry, generate_app_id
from .games_map_mixin import UNIFIDECK_TAG

if TYPE_CHECKING:
    from unifideck.core.types import Game

logger = logging.getLogger(__name__)


class _ReconcilePhasesMixin:
    """Bulk shortcut reconciliation for :class:`ShortcutService`.

    Assumes the host provides ``_load_shortcuts``, ``_load_games_map``,
    ``_save_all``, ``_ensure_shortcuts_root``,
    ``_find_existing_shortcut_key``, ``_allocate_new_shortcut_key``,
    ``_launcher_path``, ``_shortcuts``, ``_games_map``.
    """

    @staticmethod
    def _is_stale_managed_shortcut(
        entry: Any,
        valid_app_ids: set[int],
        valid_stores: set[str] | None = None,
    ) -> bool:
        """True if ``entry`` is a Unifideck-managed shortcut no longer needed.

        Identification is **LaunchOptions-based** (regex on
        ``"<store>:<game_id>"``) rather than tag-based — Steam
        can strip our ``UNIFIDECK_TAG`` on update / by user
        edit, but it preserves ``LaunchOptions`` reliably. Tag
        check is kept as a secondary signal for very old entries
        that predate the LaunchOptions convention.

        Auth shortcuts (``ubisoft:upc-auth`` and any
        ``auth-*``-tagged entry) are explicitly preserved —
        their lifecycle is owned by ``services/shortcut/shortcut.py``.

        When ``valid_stores`` is supplied, only sweep shortcuts
        whose store prefix is in that set — this is how staging
        avoided nuking the user's Epic shortcuts after they
        logged out of Epic.
        """
        from .launch_options import get_full_id, get_store_prefix

        if not isinstance(entry, dict):
            return False
        launch = entry.get("LaunchOptions", "") or ""
        full_id = get_full_id(launch) if isinstance(launch, str) else None
        if full_id == "ubisoft:upc-auth":
            return False
        tags = entry.get("tags", {})
        tags_dict = tags if isinstance(tags, dict) else {}
        is_auth_tag = any(
            str(t).startswith("auth-") for t in tags_dict.values()
        )
        if is_auth_tag:
            return False
        is_managed_by_options = full_id is not None
        is_managed_by_tag = any(
            t == UNIFIDECK_TAG for t in tags_dict.values()
        )
        if not (is_managed_by_options or is_managed_by_tag):
            return False
        if valid_stores is not None and full_id is not None:
            store = get_store_prefix(launch)
            if store and store not in valid_stores:
                return False
        return entry.get("appid") not in valid_app_ids

    async def reconcile(self: Any, games: list[Game]) -> dict[str, int]:
        """Bulk-sync all shortcuts from a list of Games.

        Computes the set-diff against current ``_games_map``:
        add missing, remove stale (only Unifideck-tagged ones
        to preserve user shortcuts). Single atomic ``_save_all``
        at the end. Returns ``{added, removed, kept, reclaimed}``
        counts.
        """
        await self._load_shortcuts()
        await self._load_games_map()

        from .registry import load_registry, save_registry

        valid_keys = {f"{g.store}:{g.store_game_id}" for g in games}
        launcher = getattr(self, "_launcher_path", "") or ""
        valid_app_ids = {
            g.app_id or generate_app_id(launcher, g.title) for g in games
        }
        valid_stores = {g.store for g in games}
        registry = load_registry()
        registry_dirty = False

        removed = self._reconcile_phase_prune_map(valid_keys)
        self._shortcuts = self._ensure_shortcuts_root(self._shortcuts)
        shortcuts_dict = self._shortcuts["shortcuts"]
        added, kept, reclaimed = self._reconcile_phase_sync_games(
            games, shortcuts_dict, registry,
        )
        if added > 0 or reclaimed > 0:
            registry_dirty = True
        removed += self._reconcile_phase_drop_stale(
            shortcuts_dict, valid_app_ids, valid_stores,
        )
        if added > 0 or removed > 0 or reclaimed > 0:
            await self._save_all()
        if registry_dirty:
            save_registry(registry)
        logger.info(
            "[ShortcutService] reconcile: %d games → "
            "added=%d kept=%d removed=%d reclaimed=%d",
            len(games), added, kept, removed, reclaimed,
        )
        return {
            "added": added, "removed": removed,
            "kept": kept, "reclaimed": reclaimed,
        }

    # ── Phase helpers ──────────────────────────────────────

    def _reconcile_phase_prune_map(
        self: Any, valid_keys: set[str],
    ) -> int:
        """Phase 1: drop ``_games_map`` keys absent from ``valid_keys``."""
        stale_keys = [k for k in self._games_map if k not in valid_keys]
        for key in stale_keys:
            del self._games_map[key]
        return len(stale_keys)

    def _reconcile_phase_sync_games(
        self: Any,
        games: list[Game],
        shortcuts_dict: dict[str, Any],
        registry: dict[str, Any],
    ) -> tuple[int, int, int]:
        """Phase 2: ensure each game has a map entry and a VDF entry."""
        from .registry import get_registered_appid, register

        added = 0
        kept = 0
        reclaimed = 0
        launcher = getattr(self, "_launcher_path", "") or ""
        for game in games:
            key = f"{game.store}:{game.store_game_id}"
            launch_options = key
            exe = game.exe_path or ""
            app_id = game.app_id or generate_app_id(launcher, game.title)
            self._games_map[key] = GameMapEntry(
                exe=exe, work_dir=game.install_path or "",
            )
            registered = get_registered_appid(registry, launch_options)
            if registered is not None:
                ord_key = self._find_existing_shortcut_key(
                    shortcuts_dict, registered,
                )
                if ord_key is not None:
                    self._reclaim_orphan(
                        shortcuts_dict[ord_key], game, registered,
                    )
                    reclaimed += 1
                    register(registry, launch_options, registered, game.title)
                    continue
            existing_key = self._find_existing_shortcut_key(
                shortcuts_dict, app_id,
            )
            if existing_key is None:
                new_key = self._allocate_new_shortcut_key(shortcuts_dict)
                shortcuts_dict[new_key] = self._build_shortcut_entry(
                    game, app_id,
                )
                added += 1
            else:
                kept += 1
            register(registry, launch_options, app_id, game.title)
        return added, kept, reclaimed

    def _reclaim_orphan(
        self: Any, entry: dict[str, Any], game: Game, app_id: int,
    ) -> None:
        """Rewrite ``entry`` in place — restores ownership while keeping AppID."""
        from .launch_options import preserve_user_params

        launcher = getattr(self, "_launcher_path", "") or ""
        current_options = entry.get("LaunchOptions", "")
        target = f"{game.store}:{game.store_game_id}"
        preserved = preserve_user_params(
            current_options if isinstance(current_options, str) else "",
            target,
        )
        entry["appid"] = app_id
        entry["AppName"] = game.title
        if launcher:
            entry["Exe"] = f'"{launcher}"'
        entry["LaunchOptions"] = preserved
        entry["icon"] = game.icon_url or entry.get("icon", "") or ""
        entry["tags"] = {
            "0": UNIFIDECK_TAG,
            "1": game.store,
            "2": "" if game.installed else "Not Installed",
        }

    def _reconcile_phase_drop_stale(
        self: Any,
        shortcuts_dict: dict[str, Any],
        valid_app_ids: set[int],
        valid_stores: set[str] | None = None,
    ) -> int:
        """Phase 3: delete Unifideck-managed shortcuts no longer needed."""
        keys_to_delete = [
            vdf_key for vdf_key, entry in shortcuts_dict.items()
            if self._is_stale_managed_shortcut(
                entry, valid_app_ids, valid_stores,
            )
        ]
        for key in keys_to_delete:
            del shortcuts_dict[key]
        return len(keys_to_delete)

    def _build_shortcut_entry(
        self: Any, game: Game, app_id: int,
    ) -> dict[str, Any]:
        """Construct a shortcuts.vdf entry dict for ``game``.

        Every Unifideck-managed shortcut points its ``Exe`` at the
        plugin's ``bin/unifideck-launcher`` script and stores the
        ``"<store>:<store_game_id>"`` token in ``LaunchOptions``.
        Anchoring on the launcher keeps the AppID stable across
        install / uninstall transitions.
        """
        launcher = getattr(self, "_launcher_path", "") or ""
        exe_quoted = f'"{launcher}"' if launcher else '""'
        start_dir = f'"{game.install_path}"' if game.install_path else '""'
        launch_options = f"{game.store}:{game.store_game_id}"
        cover_icon = game.icon_url or ""
        return {
            "appid": app_id,
            "AppName": game.title,
            "Exe": exe_quoted,
            "StartDir": start_dir,
            "icon": cover_icon,
            "ShortcutPath": "",
            "LaunchOptions": launch_options,
            "IsHidden": 0,
            "AllowDesktopConfig": 1,
            "AllowOverlay": 1,
            "OpenVR": 0,
            "Devkit": 0,
            "DevkitGameID": "",
            "DevkitOverrideAppID": 0,
            "LastPlayTime": int(time.time()),
            "FlatpakAppID": "",
            "tags": {
                "0": UNIFIDECK_TAG,
                "1": game.store,
                "2": "" if game.installed else "Not Installed",
            },
        }
