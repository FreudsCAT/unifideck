"""services/shortcut/games_map_mixin.py — Games map mutations + queries.

5 core operations mutating shortcuts list + games.map manifest
in tandem, plus ``_build_shortcut_entry`` helper. Mixin assumes
the host exposes ``_bus``, ``_shortcuts``, ``_games_map``,
paths, and async ``_load_*`` / ``_save_all`` primitives.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .games_map import GameMapEntry, generate_app_id

if TYPE_CHECKING:
    from ...core.types import Game
    from ...event_bus.event_bus import EventBus
    from .service import ShortcutService

logger = logging.getLogger(__name__)

# Signature tag written into shortcuts.vdf ``tags`` field so we
# can identify Unifideck-managed shortcuts and never touch
# user-created ones during cleanup.
UNIFIDECK_TAG = "Unifideck"


class _GamesMapMixin:
    """Games-map mutations + queries for ShortcutService."""

    # These are provided by the ShortcutService facade at runtime
    _bus: EventBus
    _shortcuts: dict[str, Any]
    _games_map: dict[str, GameMapEntry]

    # Assume host provides these async load/save primitives
    # async def _load_shortcuts(self) -> None: ...
    # async def _load_games_map(self) -> None: ...
    # async def _save_all(self) -> None: ...

    async def add_game(self: Any, game: Game) -> int:
        """Create a shortcut entry for ``game`` + register in games.map.

        Generates a stable ``app_id`` via ``generate_app_id``,
        appends to ``_shortcuts``, writes a ``GameMapEntry`` into
        ``_games_map``, persists atomically via ``_save_all``,
        emits ``SHORTCUT_CREATED``. Returns the app_id.
        """
        await self._load_shortcuts()
        await self._load_games_map()

        key = f"{game.store}:{game.id}"
        exe = game.launch_path or ""
        app_id = generate_app_id(exe, game.title)

        # Update games.map
        self._games_map[key] = GameMapEntry(exe=exe, work_dir=game.work_dir or "")

        # Update shortcuts.vdf
        if not isinstance(self._shortcuts, dict):
            self._shortcuts = {"shortcuts": {}}
        elif "shortcuts" not in self._shortcuts:
            self._shortcuts["shortcuts"] = {}

        shortcuts_dict = self._shortcuts["shortcuts"]

        # Check if it already exists and remove the old entry
        keys_to_delete = []
        for vdf_key, entry in shortcuts_dict.items():
            if isinstance(entry, dict) and entry.get("appid") == app_id:
                keys_to_delete.append(vdf_key)
            # Also remove if it matches AppName and has our tag (handling changed app_id)
            elif isinstance(entry, dict) and entry.get("AppName") == game.title:
                tags = entry.get("tags", {})
                if isinstance(tags, dict) and any(t == UNIFIDECK_TAG for t in tags.values()):
                    keys_to_delete.append(vdf_key)

        for vdf_key in keys_to_delete:
            del shortcuts_dict[vdf_key]

        # Append new entry
        new_key = str(len(shortcuts_dict))
        while new_key in shortcuts_dict:
            new_key = str(int(new_key) + 1)

        entry = self._build_shortcut_entry(game, app_id)
        shortcuts_dict[new_key] = entry

        await self._save_all()

        if self._bus:
            from ...core.types.events import Events
            self._bus.emit(
                Events.SHORTCUT_CREATED,
                store=game.store,
                app_id=app_id,
                title=game.title,
                is_auth=False,
            )

        return app_id

    async def get_exe_for_game_key(self: Any, store: str, game_id: str) -> str | None:
        """Return absolute exe path for ``store:game_id``, or None.

        Read-only query — loads the games.map on first call then
        reuses the in-memory dict.
        """
        entry = await self.get_entry_for_game_key(store, game_id)
        return entry.exe if entry else None

    async def get_entry_for_game_key(self: Any, store: str, game_id: str) -> GameMapEntry | None:
        """Return the full ``GameMapEntry`` (exe + work_dir) or None."""
        await self._load_games_map()
        key = f"{store}:{game_id}"
        return self._games_map.get(key)

    async def remove_game(self: Any, app_id: int) -> bool:
        """Remove a shortcut by ``app_id``.

        Drops from both ``_shortcuts`` and ``_games_map``, persists via
        ``_save_all``, emits ``SHORTCUT_REMOVED``. Returns True
        on success, False when the app_id wasn't known.
        """
        await self._load_shortcuts()
        await self._load_games_map()

        removed = False

        if isinstance(self._shortcuts, dict) and "shortcuts" in self._shortcuts:
            shortcuts_dict = self._shortcuts["shortcuts"]
            if isinstance(shortcuts_dict, dict):
                keys_to_delete = []
                for key, entry in shortcuts_dict.items():
                    if isinstance(entry, dict) and entry.get("appid") == app_id:
                        keys_to_delete.append(key)
                        removed = True

                for key in keys_to_delete:
                    del shortcuts_dict[key]

        # Find and remove matching entries in games.map (best effort based on app_id)
        # Note: Since games.map uses store:id as key, we need to brute force check
        keys_to_delete = []
        for key, entry in self._games_map.items():
            if generate_app_id(entry.exe, key.split(":", 1)[1]) == app_id:
                keys_to_delete.append(key)
                removed = True

        for key in keys_to_delete:
            del self._games_map[key]

        if removed:
            await self._save_all()
            if self._bus:
                from ...core.types.events import Events
                self._bus.emit(Events.SHORTCUT_REMOVED, app_id=app_id)

        return removed

    async def reconcile(self: Any, games: list[Game]) -> dict[str, int]:
        """Bulk-sync all shortcuts from a list of Games.

        Computes the set-diff against current ``_games_map``:
        add missing, remove stale (only Unifideck-tagged ones
        to preserve user shortcuts). Single atomic ``_save_all``
        at the end. Returns ``{added, removed, kept}`` counts.
        """
        await self._load_shortcuts()
        await self._load_games_map()

        added = 0
        removed = 0
        kept = 0

        # Create a set of valid game keys from the input list
        valid_keys = {f"{g.store}:{g.id}" for g in games}

        # 1. Identify stale entries in games.map
        stale_keys = [k for k in self._games_map if k not in valid_keys]
        for key in stale_keys:
            del self._games_map[key]
            removed += 1

        # 2. Add missing entries and update existing
        if not isinstance(self._shortcuts, dict):
            self._shortcuts = {"shortcuts": {}}
        elif "shortcuts" not in self._shortcuts:
            self._shortcuts["shortcuts"] = {}

        shortcuts_dict = self._shortcuts["shortcuts"]

        for game in games:
            key = f"{game.store}:{game.id}"
            exe = game.launch_path or ""
            app_id = generate_app_id(exe, game.title)

            # Update games.map
            self._games_map[key] = GameMapEntry(exe=exe, work_dir=game.work_dir or "")

            # Find existing shortcut
            existing_key = None
            for vdf_key, entry in shortcuts_dict.items():
                if isinstance(entry, dict) and entry.get("appid") == app_id:
                    existing_key = vdf_key
                    break

            if existing_key is None:
                # Need to add
                new_key = str(len(shortcuts_dict))
                while new_key in shortcuts_dict:
                    new_key = str(int(new_key) + 1)
                shortcuts_dict[new_key] = self._build_shortcut_entry(game, app_id)
                added += 1
            else:
                kept += 1

        # 3. Clean up unmanaged shortcuts (stale)
        # Scan shortcuts for any that have UNIFIDECK_TAG but are NOT in our valid games list
        valid_app_ids = {
            generate_app_id(g.launch_path or "", g.title)
            for g in games
        }

        keys_to_delete = []
        for vdf_key, entry in shortcuts_dict.items():
            if not isinstance(entry, dict):
                continue

            tags = entry.get("tags", {})
            if not isinstance(tags, dict):
                continue

            # Skip if not unifideck-managed or if it's an auth shortcut
            is_managed = any(t == UNIFIDECK_TAG for t in tags.values())
            is_auth = any(str(t).startswith("auth-") for t in tags.values())

            if is_managed and not is_auth:
                app_id = entry.get("appid")
                if app_id not in valid_app_ids:
                    keys_to_delete.append(vdf_key)

        for key in keys_to_delete:
            del shortcuts_dict[key]
            # Account for removed shortcuts
            removed += 1

        if added > 0 or removed > 0:
            await self._save_all()

        return {"added": added, "removed": removed, "kept": kept}

    def _build_shortcut_entry(self: Any, game: Game, app_id: int) -> dict[str, Any]:
        """Construct a shortcuts.vdf entry dict for ``game``.

        Populates ``appid``, ``AppName``, ``Exe``, ``StartDir``,
        ``tags`` (including ``UNIFIDECK_TAG``). Arguments that
        land in shortcuts.vdf are quoted/escaped by the serialiser
        downstream — no shell-escaping here.
        """
        # Exe should be in quotes for steam
        exe_path = f'"{game.launch_path}"' if game.launch_path else '""'
        start_dir = f'"{game.work_dir}"' if game.work_dir else '""'

        return {
            "appid": app_id,
            "AppName": game.title,
            "Exe": exe_path,
            "StartDir": start_dir,
            "icon": "",
            "ShortcutPath": "",
            "LaunchOptions": "",
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
            },
        }
