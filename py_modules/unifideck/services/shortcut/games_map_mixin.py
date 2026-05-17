"""services/shortcut/games_map_mixin.py — Games map mutations + queries.

5 core operations mutating shortcuts list + games.map manifest
in tandem, plus ``_build_shortcut_entry`` helper. Mixin assumes
the host exposes ``_bus``, ``_shortcuts``, ``_games_map``,
paths, and async ``_load_*`` / ``_save_all`` primitives.

Refactor history (2026-05-14): ``add_game`` was a single async
method at CC=17 — it inlined the shortcuts dict normalisation,
the obsolete-entry sweep (two-branch matching: by app_id and by
AppName+tag), the new key allocation, and the bus emit. Pulled
the obsolete-key search and tag check into private helpers; the
shape-normalisation and key allocation reuse existing helpers
(``_ensure_shortcuts_root``, ``_allocate_new_shortcut_key``)
that were already defined for ``reconcile``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .games_map import GameMapEntry, generate_app_id
from .reconcile_phases import _ReconcilePhasesMixin

if TYPE_CHECKING:
    from unifideck.core.types import Game
    from unifideck.event_bus.event_bus import EventBus


logger = logging.getLogger(__name__)

# Signature tag written into shortcuts.vdf ``tags`` field so we
# can identify Unifideck-managed shortcuts and never touch
# user-created ones during cleanup.
UNIFIDECK_TAG = "Unifideck"


class _GamesMapMixin(_ReconcilePhasesMixin):
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

        key = f"{game.store}:{game.app_id}"
        exe = game.exe_path or ""
        app_id = generate_app_id(exe, game.title)

        # Update games.map first — even if the shortcuts.vdf write
        # below fails, the canonical record of "what Unifideck owns"
        # is correct.
        # ``work_dir`` is the directory the launcher cd's into before
        # starting the exe — for installed games it's the install
        # directory itself (where the launcher binary sits next to
        # the game data).
        self._games_map[key] = GameMapEntry(
            exe=exe, work_dir=game.install_path or "",
        )

        # Normalise the shortcuts dict shape (tolerates corrupt VDF
        # produced by a third party clobbering the file).
        self._shortcuts = self._ensure_shortcuts_root(self._shortcuts)
        shortcuts_dict = self._shortcuts["shortcuts"]

        # Remove any prior entry that would now collide.
        for vdf_key in self._find_obsolete_keys(
            shortcuts_dict, app_id, game.title,
        ):
            del shortcuts_dict[vdf_key]

        # Allocate a fresh ordinal key and store the new entry.
        new_key = self._allocate_new_shortcut_key(shortcuts_dict)
        shortcuts_dict[new_key] = self._build_shortcut_entry(game, app_id)

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

    # ─────────────────────────────────────────────────────────────
    # Helpers extracted from the former CC=17 add_game
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _find_obsolete_keys(
        shortcuts_dict: dict[str, Any],
        app_id: int,
        title: str,
    ) -> list[str]:
        """Return shortcut keys to replace when adding ``app_id``/``title``.

        Two collision cases trigger a deletion:

            * ``appid`` matches — straightforward reinstall or
              metadata refresh; we drop the old entry so the new
              one takes its slot (Steam itself tolerates duplicate
              ``appid`` rows but the UI shows two tiles).
            * ``AppName`` matches AND the entry carries the
              ``UNIFIDECK_TAG`` — same game, different app_id.
              This happens when ``game.launch_path`` changes
              (e.g. game moved to SD card): ``generate_app_id``
              produces a new hash and the previous tile becomes
              orphaned. We only delete tagged entries to leave
              user-created shortcuts alone.
        """
        obsolete: list[str] = []
        for vdf_key, entry in shortcuts_dict.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("appid") == app_id:
                obsolete.append(vdf_key)
                continue
            if (
                entry.get("AppName") == title
                and _GamesMapMixin._entry_has_unifideck_tag(entry)
            ):
                obsolete.append(vdf_key)
        return obsolete

    @staticmethod
    def _entry_has_unifideck_tag(entry: dict[str, Any]) -> bool:
        """Whether a VDF entry is tagged as Unifideck-managed.

        Defensive: tags can be missing (user-created shortcut),
        ``None`` (legacy entries), or a non-dict value (corrupt
        file) — all three reduce to "not managed".
        """
        tags = entry.get("tags", {})
        if not isinstance(tags, dict):
            return False
        return any(t == UNIFIDECK_TAG for t in tags.values())

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
        # cast: ``self`` is typed Any in the mixin (host provides
        # _load_games_map), so ``self._games_map.get`` returns Any
        # even though ``_games_map: dict[str, GameMapEntry]`` is
        # declared at class scope. Cast to recover the precise type.
        entry: GameMapEntry | None = self._games_map.get(key)
        return entry

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

        # bool() cast: ``removed`` is ``bool | Any`` because the
        # dropped_* helpers are typed bool but ``self: Any`` taints
        # the dataflow inference. Explicit cast keeps the return
        # type narrow.
        return bool(removed)

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
        # cast: ``shortcuts`` is ``Any`` on entry; after the two
        # branches above it's guaranteed to be a ``dict[str, Any]``
        # with a "shortcuts" key. The explicit annotation tells mypy.
        result: dict[str, Any] = shortcuts
        return result

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
