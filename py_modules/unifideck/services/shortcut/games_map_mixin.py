"""Games-map mixin — high-level shortcut CRUD operations.

OP-14e | py_modules/unifideck/services/shortcut/games_map_mixin.py

``_GamesMapMixin`` provides the public CRUD operations on top of
the two flat in-memory data structures owned by ``ShortcutService``:

* ``_shortcuts`` — the list of dicts that mirrors
  ``shortcuts.vdf``;
* ``_games_map`` — the ``"<store>:<game_id>" → GameMapEntry``
  mapping that lets us go from a Unifideck game key to its
  Steam-side AppID and back.

Every mutating operation here loads the two structures lazily
(via the host class's ``_load_*`` methods), mutates them, saves
everything atomically via ``_save_all``, then optionally emits a
bus event.

The ``UNIFIDECK_TAG`` constant is the Steam-side "tag" used to
mark shortcuts as Unifideck-managed — ``reconcile`` only removes
shortcuts carrying this tag, so shortcuts added through other
means (Steam's own "Add a Non-Steam Game" dialog) are preserved.
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
    """CRUD over the in-memory shortcuts list and games map."""

    _bus: EventBus
    _shortcuts: list[dict[str, Any]]
    _games_map: dict[str, GameMapEntry]

    async def add_game(self, game: Game) -> Any:
        """Create or update a Steam shortcut for a game.

        Workflow:

        1. Refuse if the game has no resolved ``exe_path``
           (``no_executable``).
        2. Derive the AppID deterministically from
           ``(exe_path, title)``.
        3. Lazy-load the shortcuts + games map.
        4. If a shortcut with this AppID already exists, replace
           it in place (idempotent re-install); otherwise append.
        5. Update the games-map entry.
        6. Persist atomically and emit ``GAME_INSTALLED``.

        Args:
            game: the ``Game`` record describing the install.

        Returns:
            ``Result(success=True)`` on success or
            ``Result(success=False, error="no_executable")`` when
            the game has no launchable binary.
        """
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
        """Return the executable path for a ``(store, game_id)`` key.

        Thin convenience wrapper over ``get_entry_for_game_key``
        that surfaces only the ``exe`` field. Used by the
        launcher service to find what to run.

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            Absolute path to the executable, or ``None`` if no
            entry exists for this key.
        """
        entry = await self.get_entry_for_game_key(store, game_id)
        return entry.exe if entry else None

    async def get_entry_for_game_key(
        self,
        store: str,
        game_id: str,
    ) -> GameMapEntry | None:
        """Return the full games-map entry for a ``(store, game_id)`` key.

        Lazy-loads the games map on first call. Returns ``None``
        when the game isn't tracked (typical case for games
        installed before Unifideck was set up, or via a different
        tool).

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            The ``GameMapEntry`` or ``None``.
        """
        await self._load_games_map()
        return self._games_map.get(f"{store}:{game_id}")

    async def remove_game(self, app_id: int) -> Any:
        """Remove the shortcut with the given Steam AppID.

        Filters the shortcuts list rather than mutating in place
        (immutable-style: builds a new list excluding the matching
        AppID). Returns ``not_found`` if no shortcut matched —
        useful to surface to the user when a manual delete
        attempts to remove an already-removed entry.

        Note: this does **not** clean up the games-map entry —
        ``reconcile`` is the canonical cleanup path on the next
        library sync.

        Args:
            app_id: Steam AppID of the shortcut to remove.

        Returns:
            ``Result(success=True)`` on successful removal,
            ``Result(success=False, error="not_found")`` if no
            shortcut had that AppID.
        """
        from ...core.types import Result

        await self._load_shortcuts()
        before = len(self._shortcuts)
        self._shortcuts = [s for s in self._shortcuts if s.get("appid") != app_id]
        if len(self._shortcuts) == before:
            return Result(success=False, error="not_found")
        await self._save_all()
        return Result(success=True)

    async def reconcile(self, games: list[Game]) -> Any:
        """Reconcile shortcuts + games-map with a fresh library snapshot.

        Two-pass algorithm:

        **Shortcuts pass:**

        1. Build the target AppID set from the games list (only
           installed games with an exe path qualify).
        2. Keep every existing shortcut that's either:
           - **not** tagged as Unifideck-managed (preserve
             user-added entries), **or**
           - in the target set (still installed).
        3. Append any AppID in the target set that doesn't have
           an existing shortcut yet.

        **Games-map pass:**

        4. Drop entries no longer in the target.
        5. Insert/update entries for everything in the target.

        Atomic: a single ``_save_all`` at the end commits both
        passes so they can't diverge.

        Args:
            games: fresh library snapshot from the sync event.

        Returns:
            ``Result(success=True)``.
        """
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
        """Build the dict representing one shortcut.vdf entry.

        Encodes the canonical shortcut shape Steam expects:

        * ``Exe`` and ``StartDir`` are quoted strings (Steam's VDF
          format requires the quotes for paths with spaces).
        * ``LaunchOptions`` carries the ``"<store>:<game_id>"``
          string — Unifideck's RPC layer parses this back when
          intercepting Steam launches.
        * Tags include ``UNIFIDECK_TAG`` (used by ``reconcile``
          for identification) and the capitalised store name (for
          UI category display in Steam).

        Args:
            game: the ``Game`` record.
            app_id: pre-computed Steam AppID.

        Returns:
            Shortcut dict ready to be inserted into ``_shortcuts``.
        """
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
