"""Game-map mixin — CRUD over the (store, game_id) → AppID map.

OP-14e | py_modules/unifideck/services/shortcut/games_map_mixin.py

``_GamesMapMixin`` exposes the methods the rest of the plugin uses to
manipulate the game map :

* ``register_shortcut`` — add or update an entry;
* ``unregister_shortcut`` — remove an entry + drop from shortcuts.vdf;
* ``find_appid_for`` — lookup by (store, game_id);
* ``find_game_for_appid`` — reverse lookup;
* ``invalidate_appid`` — mark an entry stale (recovery path).

State changes are committed to disk via ``persistence`` and announced
on the bus via ``events``.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from .games_map import GameMapEntry, generate_app_id

if TYPE_CHECKING:
    from ...core.types import Game
    from ...event_bus.event_bus import EventBus
logger = logging.getLogger(__name__)
UNIFIDECK_TAG = "Unifideck"


class _GamesMapMixin:
    """Games map mixin."""

    _bus: EventBus
    _shortcuts: list[dict[str, Any]]
    _games_map: dict[str, GameMapEntry]

    async def add_game(self, game: Game) -> Any:
        """Add game."""
        from ...core.types import Events, Result

        if not game.exe_path:
            return Result(success=False, error="no_executable")
        app_id = generate_app_id(game.exe_path, game.title)
        await self._load_shortcuts()
        await self._load_games_map()
        entry = self._build_shortcut_entry(game, app_id)
        existing_idx = next(
            (i for i, s in enumerate(self._shortcuts) if s.get("appid") == app_id),
            None,
        )
        if existing_idx is not None:
            self._shortcuts[existing_idx] = entry
        else:
            self._shortcuts.append(entry)
        work_dir = game.install_path or str(Path(game.exe_path).parent)
        key = f"{game.store}:{game.store_game_id}"
        self._games_map[key] = GameMapEntry(
            exe=game.exe_path,
            work_dir=work_dir,
        )
        await self._save_all()
        await self._bus.emit(
            Events.GAME_INSTALLED,
            store=game.store,
            game_id=game.store_game_id,
            app_id=app_id,
        )
        return Result(success=True)

    async def get_exe_for_game_key(self, store: str, game_id: str) -> str | None:
        """Get exe for game key."""
        entry = await self.get_entry_for_game_key(store, game_id)
        return entry.exe if entry else None

    async def get_entry_for_game_key(
        self,
        store: str,
        game_id: str,
    ) -> GameMapEntry | None:
        """Get entry for game key."""
        await self._load_games_map()
        return self._games_map.get(f"{store}:{game_id}")

    async def remove_game(self, app_id: int) -> Any:
        """Remove game."""
        from ...core.types import Result

        await self._load_shortcuts()
        before = len(self._shortcuts)
        self._shortcuts = [s for s in self._shortcuts if s.get("appid") != app_id]
        if len(self._shortcuts) == before:
            return Result(success=False, error="not_found")
        await self._save_all()
        return Result(success=True)

    async def reconcile(self, games: list[Game]) -> Any:
        """Reconcile."""
        from ...core.types import Result

        await self._load_shortcuts()
        await self._load_games_map()
        target_ids = {
            generate_app_id(g.exe_path, g.title): g
            for g in games
            if g.exe_path and g.installed
        }
        kept = [
            s
            for s in self._shortcuts
            if (UNIFIDECK_TAG not in s.get("tags", {}).values())
            or (s.get("appid") in target_ids)
        ]
        existing_ids = {s.get("appid") for s in kept}
        for app_id, game in target_ids.items():
            if app_id not in existing_ids:
                kept.append(
                    self._build_shortcut_entry(game, app_id),
                )
        self._shortcuts = kept
        target_keys = {
            f"{g.store}:{g.store_game_id}": g
            for g in games
            if g.exe_path and g.installed
        }
        for stale in list(self._games_map.keys()):
            if stale not in target_keys:
                del self._games_map[stale]
        for key, g in target_keys.items():
            exe_path = g.exe_path
            assert exe_path is not None
            work_dir = g.install_path or str(Path(exe_path).parent)
            self._games_map[key] = GameMapEntry(
                exe=exe_path,
                work_dir=work_dir,
            )
        await self._save_all()
        logger.info(
            "[ShortcutService] reconciled %d shortcuts",
            len(kept),
        )
        return Result(success=True)

    def _build_shortcut_entry(self, game: Game, app_id: int) -> dict[str, Any]:
        """Build shortcut entry."""
        return {
            "appid": app_id,
            "AppName": game.title,
            "Exe": f'"{game.exe_path}"',
            "StartDir": f'"{game.install_path or ""}"',
            "LaunchOptions": (f"{game.store}:{game.store_game_id}"),
            "icon": game.icon_url or "",
            "tags": {
                "0": UNIFIDECK_TAG,
                "1": game.store.capitalize(),
            },
        }
