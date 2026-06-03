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

from .games_map import UNIFIDECK_TAG, GameMapEntry, generate_app_id
from .launch_options import get_full_id

if TYPE_CHECKING:
    from unifideck.core.types import Game

logger = logging.getLogger(__name__)


def _build_launch_index(shortcuts_dict: dict[str, Any]) -> dict[str, str]:
    """Map ``"store:game_id"`` → VDF ordinal key for every shortcut.

    One O(N) pass over ``shortcuts_dict`` so per-game lookups in
    ``_sync_one_game`` are O(1). Entries with missing or
    non-string ``LaunchOptions`` and entries whose
    ``LaunchOptions`` doesn't parse as Unifideck form are
    skipped silently.
    """
    launch_to_key: dict[str, str] = {}
    for vdf_key, entry in shortcuts_dict.items():
        if not isinstance(entry, dict):
            continue
        launch = entry.get("LaunchOptions", "")
        if not isinstance(launch, str) or not launch:
            continue
        full_id = get_full_id(launch)
        if full_id:
            launch_to_key[full_id] = vdf_key
    return launch_to_key


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
        from .protected import is_protected

        if not isinstance(entry, dict):
            return False
        launch = entry.get("LaunchOptions", "") or ""
        full_id = get_full_id(launch) if isinstance(launch, str) else None
        # Centralised protected-set check — replaces the previous
        # hardcoded ``ubisoft:upc-auth`` literal so new stores can
        # register their auth shortcuts in one place.
        if is_protected(full_id):
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

    async def reconcile(
        self: Any, games: list[Game], *, force: bool = False,
    ) -> dict[str, int]:
        """Bulk-sync all shortcuts from a list of Games.

        Regular sync (``force=False``): **additive** — new
        ``store:game_id`` pairs get a shortcut; already-existing
        entries are left untouched. Mirrors staging's
        ``add_games_batch`` behavior.

        Force sync (``force=True``): **overwriting** — existing
        entries have their ``AppName``, ``exe``, ``tags``, and
        ``icon`` fields updated to match current metadata, while
        preserving their ``appid`` so artwork and playtime survive
        the rewrite. Mirrors staging's ``force_update_games_batch``.
        """
        await self._load_shortcuts()
        await self._load_games_map()

        from .registry import load_registry, save_registry

        valid_keys = {f"{g.store}:{g.store_game_id}" for g in games}
        launcher = getattr(self, "_launcher_path", "") or ""
        valid_app_ids = {
            g.app_id or generate_app_id(
                launcher, f"{g.store}:{g.store_game_id}",
            )
            for g in games
        }
        valid_stores = {g.store for g in games}
        registry = load_registry()
        registry_dirty = False

        removed = self._reconcile_phase_prune_map(valid_keys)
        self._shortcuts = self._ensure_shortcuts_root(self._shortcuts)
        shortcuts_dict = self._shortcuts["shortcuts"]
        added, kept, reclaimed = self._reconcile_phase_sync_games(
            games, shortcuts_dict, registry, force=force,
        )
        if added > 0 or reclaimed > 0:
            registry_dirty = True
        removed += self._reconcile_phase_drop_stale(
            shortcuts_dict, valid_app_ids, valid_stores,
        )
        # Dedup pass — Steam occasionally creates duplicate VDF
        # entries with the same launch-options (in-memory desync,
        # crash recovery). Score by metadata richness, drop losers.
        # Runs AFTER add/drop so reclaimed orphans count toward the
        # winners' scores.
        from .dedup import find_duplicate_losers
        dedup_losers = find_duplicate_losers(shortcuts_dict)
        for loser_key in dedup_losers:
            shortcuts_dict.pop(loser_key, None)
        removed += len(dedup_losers)
        if added > 0 or removed > 0 or reclaimed > 0:
            await self._save_all()
        if registry_dirty:
            save_registry(registry)
        logger.info(
            "[ShortcutService] reconcile: %d games → "
            "added=%d kept=%d removed=%d reclaimed=%d",
            len(games), added, kept, removed, reclaimed,
        )
        # Diagnostic banner — when reading ``~/homebrew/logs/`` to
        # debug "my new games aren't showing up", the most common
        # answer is "restart Steam". Staging printed this verbatim;
        # the frontend modal handles the user-facing prompt but
        # the log banner keeps the diagnostic obvious in tailed logs.
        if added > 0 or removed > 0:
            for line in (
                "=" * 60,
                "IMPORTANT: Steam restart required to see "
                "shortcut changes!",
                f"  (added={added} removed={removed} reclaimed={reclaimed})",
                "Please EXIT Steam completely and restart for the "
                "shortcuts.vdf changes to take effect.",
                "=" * 60,
            ):
                logger.warning("[ShortcutService] %s", line)
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
        *,
        force: bool = False,
    ) -> tuple[int, int, int]:
        """Phase 2: ensure each game has a map entry and a VDF entry.

        Matching (both staging behaviours):

        * **LaunchOptions** — ``store:game_id`` from the shortcut's
          ``LaunchOptions`` field. Stable across title changes; the
          primary match key. When found, we KNOW this game already
          has a shortcut even if the app_id has drifted.
        * **AppID** — the integer stored in ``shortcut["appid"]``.
          Falls back when LaunchOptions is missing or corrupted.

        Regular sync (``force=False``): matches existing shortcuts
        and **keeps** them — ``added`` only ticks for truly new
        ``store:game_id`` pairs. Moves ``kept`` for existing matches.
        Mirrors staging's ``add_games_batch(skip if exists)``.

        Force sync (``force=True``): matches existing shortcuts and
        **updates** their ``AppName``, ``exe``, ``tags``, and
        ``icon`` fields to match current metadata, while preserving
        the ``appid`` so artwork and playtime carry through the
        rewrite. Mirrors staging's ``force_update_games_batch``.
        """
        # Build a lookup of LaunchOptions → shortcut_key BEFORE
        # iterating games — one O(N) pass across shortcuts, then
        # O(1) per-game. Mirrors staging's approach at
        # shortcuts_manager.py line 1708-1713.
        launch_to_key = _build_launch_index(shortcuts_dict)
        launcher = getattr(self, "_launcher_path", "") or ""
        added = kept = reclaimed = 0
        for game in games:
            outcome = self._sync_one_game(
                game, shortcuts_dict, registry, launch_to_key,
                launcher=launcher, force=force,
            )
            if outcome == "added":
                added += 1
            elif outcome == "reclaimed":
                reclaimed += 1
            else:
                kept += 1
        return added, kept, reclaimed

    def _sync_one_game(
        self: Any,
        game: Game,
        shortcuts_dict: dict[str, Any],
        registry: dict[str, Any],
        launch_to_key: dict[str, str],
        *,
        launcher: str,
        force: bool,
    ) -> str:
        """Reconcile a single ``game`` into ``shortcuts_dict``.

        Returns one of ``"added"``, ``"kept"``, ``"reclaimed"`` for
        the caller's tally. Side effects: updates ``self._games_map``,
        ``shortcuts_dict``, ``registry``.
        """
        from .registry import get_registered_appid, register

        key = f"{game.store}:{game.store_game_id}"
        exe = game.exe_path or ""
        app_id = game.app_id or generate_app_id(launcher, key)

        # games.map is the launcher's exe-path lookup, only for games
        # that have a local executable. xCloud (Microsoft) titles need
        # no row — the dispatcher synthesizes their launch context from
        # ``store == "microsoft"`` (see launcher/dispatcher.py).
        #
        # Crucially, library-sourced sync ``Game`` objects do NOT carry
        # ``exe_path`` (it's resolved once at install time by the
        # download worker and written via ``mark_installed``). So an
        # installed game arriving here with an empty ``exe`` must NOT
        # wipe its existing entry — doing so was the bug that silently
        # dropped install-time games.map rows on the next sync (e.g.
        # Amazon vanished after a post-install library sync). Preserve
        # the existing row; only drop when the game is truly uninstalled.
        if game.installed and exe:
            self._games_map[key] = GameMapEntry(
                exe=exe, work_dir=game.install_path or "", app_id=app_id,
            )
        elif game.installed and key in self._games_map:
            # Keep the install-time entry; this sync just lacks the exe.
            pass
        else:
            self._games_map.pop(key, None)

        # ── Reclaim orphan by registry ──────────────────────
        registered = get_registered_appid(registry, key)
        if registered is not None:
            ord_key = self._find_existing_shortcut_key(
                shortcuts_dict, registered,
            )
            if ord_key is not None:
                self._reclaim_orphan(
                    shortcuts_dict[ord_key], game, registered,
                )
                register(registry, key, registered, game.title)
                return "reclaimed"

        # ── Match by LaunchOptions (primary — staging behaviour)
        existing_key = launch_to_key.get(key)
        if existing_key is not None:
            if force:
                self._update_existing_shortcut(
                    shortcuts_dict[existing_key], game, app_id, launcher,
                )
                register(registry, key, app_id, game.title)
            return "kept"

        # ── Match by AppID (fallback — LaunchOptions missing)
        existing_key = self._find_existing_shortcut_key(
            shortcuts_dict, app_id,
        )
        if existing_key is not None:
            if force:
                self._update_existing_shortcut(
                    shortcuts_dict[existing_key], game, app_id, launcher,
                )
            register(registry, key, app_id, game.title)
            return "kept"

        # ── New shortcut
        new_key = self._allocate_new_shortcut_key(shortcuts_dict)
        shortcuts_dict[new_key] = self._build_shortcut_entry(game, app_id)
        register(registry, key, app_id, game.title)
        return "added"

    def _update_existing_shortcut(
        self: Any,
        entry: dict[str, Any],
        game: Game,
        app_id: int,
        launcher: str,
    ) -> None:
        """Force-update an existing shortcut in-place — preserves ``appid``.

        Only called during force sync (``force=True``).
        Updates ``AppName``, ``Exe``, ``tags``, and ``LaunchOptions``
        while leaving ``appid`` unchanged so artwork files and Steam's
        playtime tracking survive the rewrite. The ``icon`` field is
        set from ``game.icon_url`` when available (the artwork phase
        populates it from on-disk grid files).
        """
        exe_quoted = f'"{launcher}"' if launcher else '""'
        entry["AppName"] = game.title
        entry["Exe"] = exe_quoted
        entry["LaunchOptions"] = f"{game.store}:{game.store_game_id}"
        if game.icon_url:
            entry["icon"] = game.icon_url
        tags_dict = entry.get("tags", {})
        if isinstance(tags_dict, dict):
            tags_dict["0"] = UNIFIDECK_TAG
            tags_dict["1"] = game.store
            tags_dict["2"] = "" if game.installed else "Not Installed"

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
