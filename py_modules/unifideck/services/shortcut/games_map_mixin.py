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
    from unifideck.core.types import Game
    from unifideck.event_bus.event_bus import EventBus


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
            from unifideck.core.types.events import Events
            await self._bus.emit(
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

    @staticmethod
    def _drop_shortcut_entries(
        shortcuts: Any,
        app_id: int,
    ) -> bool:
        """Delete every ``shortcuts.vdf`` entry matching ``app_id``.

        Tolerates corrupt VDF (non-dict at root or under
        ``"shortcuts"``) by treating those branches as "no match".
        Returns True if at least one entry was deleted.
        """
        if not isinstance(shortcuts, dict) or "shortcuts" not in shortcuts:
            return False
        shortcuts_dict = shortcuts["shortcuts"]
        if not isinstance(shortcuts_dict, dict):
            return False
        keys_to_delete = [
            key for key, entry in shortcuts_dict.items()
            if isinstance(entry, dict) and entry.get("appid") == app_id
        ]
        for key in keys_to_delete:
            del shortcuts_dict[key]
        return bool(keys_to_delete)

    def _drop_games_map_entries(self: Any, app_id: int) -> bool:
        """Delete every ``games.map`` entry whose derived app_id matches.

        ``games.map`` is keyed by ``"<store>:<game_id>"`` so we
        can't look the app_id up directly — we recompute it for
        every entry and compare. The number of entries is small
        enough (low hundreds at most) that this O(n) sweep is
        fine. Returns True if at least one entry was deleted.
        """
        keys_to_delete = [
            key for key, entry in self._games_map.items()
            if generate_app_id(entry.exe, key.split(":", 1)[1]) == app_id
        ]
        for key in keys_to_delete:
            del self._games_map[key]
        return bool(keys_to_delete)

    async def remove_game(self: Any, app_id: int) -> bool:
        """Remove a shortcut by ``app_id``.

        Drops from both ``_shortcuts`` and ``_games_map``, persists via
        ``_save_all``, emits ``SHORTCUT_REMOVED``. Returns True
        on success, False when the app_id wasn't known.
        """
        await self._load_shortcuts()
        await self._load_games_map()

        dropped_vdf = self._drop_shortcut_entries(self._shortcuts, app_id)
        dropped_map = self._drop_games_map_entries(app_id)
        removed = dropped_vdf or dropped_map

        if removed:
            await self._save_all()
            if self._bus:
                from unifideck.core.types.events import Events
                await self._bus.emit(Events.SHORTCUT_REMOVED, app_id=app_id)

        return removed

    @staticmethod
    def _ensure_shortcuts_root(shortcuts: Any) -> dict[str, Any]:
        """Return the ``shortcuts`` sub-dict, initialising if needed.

        Steam's ``shortcuts.vdf`` always wraps entries under a top-
        level ``"shortcuts"`` key. We tolerate two breakage modes
        seen in the wild (file completely overwritten by a third
        party, or the wrapper key missing) by re-establishing the
        canonical shape and returning the inner dict that the rest
        of ``reconcile`` mutates.
        """
        if not isinstance(shortcuts, dict):
            shortcuts = {"shortcuts": {}}
        elif "shortcuts" not in shortcuts:
            shortcuts["shortcuts"] = {}
        return shortcuts

    @staticmethod
    def _find_existing_shortcut_key(
        shortcuts_dict: dict[str, Any],
        app_id: int,
    ) -> str | None:
        """Find the existing ``shortcuts.vdf`` key for a given app_id.

        Steam keys shortcuts by an opaque ordinal string ("0", "1",
        ...); the canonical identifier is ``appid``. Returns the
        ordinal of the entry matching ``app_id``, or ``None`` if
        no entry exists yet (caller will allocate a fresh ordinal).
        """
        for vdf_key, entry in shortcuts_dict.items():
            if isinstance(entry, dict) and entry.get("appid") == app_id:
                return vdf_key
        return None

    @staticmethod
    def _allocate_new_shortcut_key(shortcuts_dict: dict[str, Any]) -> str:
        """Pick the next free ordinal string key for a new shortcut.

        Starts at ``len(shortcuts_dict)`` (cheap O(1) guess) and
        increments past collisions. This keeps keys roughly dense
        without requiring a global counter.
        """
        new_key = str(len(shortcuts_dict))
        while new_key in shortcuts_dict:
            new_key = str(int(new_key) + 1)
        return new_key

    @staticmethod
    def _is_stale_managed_shortcut(
        entry: Any,
        valid_app_ids: set[int],
    ) -> bool:
        """True if ``entry`` is a Unifideck-managed shortcut that no
        longer corresponds to any game in the current library.

        Filters out non-dict entries (corrupt VDF), entries without
        a ``tags`` dict (user-created), entries tagged ``auth-*``
        (used by the OAuth flows and never reconciled here), and
        entries whose ``appid`` is still in ``valid_app_ids``.
        """
        if not isinstance(entry, dict):
            return False
        tags = entry.get("tags", {})
        if not isinstance(tags, dict):
            return False
        is_managed = any(t == UNIFIDECK_TAG for t in tags.values())
        is_auth = any(str(t).startswith("auth-") for t in tags.values())
        if not is_managed or is_auth:
            return False
        return entry.get("appid") not in valid_app_ids

    async def reconcile(self: Any, games: list[Game]) -> dict[str, int]:
        """Bulk-sync all shortcuts from a list of Games.

        Computes the set-diff against current ``_games_map``:
        add missing, remove stale (only Unifideck-tagged ones
        to preserve user shortcuts). Single atomic ``_save_all``
        at the end. Returns ``{added, removed, kept}`` counts.

        Implementation breakdown:
          1. Drop ``games.map`` keys not in the new library.
          2. For each game: create-or-keep the ``shortcuts.vdf``
             entry, refresh ``games.map``.
          3. Drop ``shortcuts.vdf`` entries tagged as Unifideck-
             managed but absent from the new library.
        """
        await self._load_shortcuts()
        await self._load_games_map()

        valid_keys = {f"{g.store}:{g.id}" for g in games}
        valid_app_ids = {
            generate_app_id(g.launch_path or "", g.title) for g in games
        }

        # ── Phase 1: prune games.map ──────────────────────────────
        removed = 0
        stale_keys = [k for k in self._games_map if k not in valid_keys]
        for key in stale_keys:
            del self._games_map[key]
            removed += 1

        # ── Phase 2: add or keep entries for every current game ──
        self._shortcuts = self._ensure_shortcuts_root(self._shortcuts)
        shortcuts_dict = self._shortcuts["shortcuts"]

        added = 0
        kept = 0
        for game in games:
            key = f"{game.store}:{game.id}"
            exe = game.launch_path or ""
            app_id = generate_app_id(exe, game.title)
            self._games_map[key] = GameMapEntry(exe=exe, work_dir=game.work_dir or "")

            if self._find_existing_shortcut_key(shortcuts_dict, app_id) is None:
                new_key = self._allocate_new_shortcut_key(shortcuts_dict)
                shortcuts_dict[new_key] = self._build_shortcut_entry(game, app_id)
                added += 1
            else:
                kept += 1

        # ── Phase 3: drop unmanaged-or-stale shortcuts ───────────
        keys_to_delete = [
            vdf_key for vdf_key, entry in shortcuts_dict.items()
            if self._is_stale_managed_shortcut(entry, valid_app_ids)
        ]
        for key in keys_to_delete:
            del shortcuts_dict[key]
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
