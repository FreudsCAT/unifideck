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

from .games_map import UNIFIDECK_TAG, GameMapEntry, generate_app_id
from .launch_options import get_full_id, preserve_user_params
from .reconcile_phases import _ReconcilePhasesMixin

if TYPE_CHECKING:
    from unifideck.core.types import Game
    from unifideck.event_bus.event_bus import EventBus


logger = logging.getLogger(__name__)

# Re-exported from ``games_map`` so callers can still write
# ``from .games_map_mixin import UNIFIDECK_TAG`` without forming
# a cycle with ``reconcile_phases``.
__all__ = ["UNIFIDECK_TAG"]


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
            exe=exe, work_dir=game.install_path or "", app_id=app_id,
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
        """Delete every ``games.map`` entry whose stored app_id matches.

        v3 entries store ``app_id`` directly. Legacy v1/v2 rows
        carry ``app_id == 0`` and are matched by cross-referencing
        the loaded ``shortcuts.vdf``: if any VDF entry with the
        target ``app_id`` has the same ``Exe`` as the games.map
        row, treat it as a match (covers the orphan-backfill case
        before the next ``_save_all`` writes the v3 row).
        Returns True if at least one entry was deleted.
        """
        legacy_exes: set[str] = set()
        shortcuts_root = self._shortcuts.get("shortcuts") if isinstance(
            self._shortcuts, dict,
        ) else None
        if isinstance(shortcuts_root, dict):
            for entry in shortcuts_root.values():
                if not isinstance(entry, dict):
                    continue
                if entry.get("appid") == app_id:
                    exe = entry.get("Exe") or entry.get("exe") or ""
                    if isinstance(exe, str):
                        legacy_exes.add(exe.strip('"'))

        keys_to_delete: list[str] = []
        for key, entry in self._games_map.items():
            if entry.app_id == app_id:
                keys_to_delete.append(key)
                continue
            if entry.app_id == 0 and entry.exe in legacy_exes:
                keys_to_delete.append(key)
        for key in keys_to_delete:
            del self._games_map[key]
        return bool(keys_to_delete)

    async def mark_installed(
        self: Any,
        store: str,
        store_game_id: str,
        title: str,
        exe_path: str,
        install_path: str,
    ) -> int | None:
        """Flip an existing shortcut to "installed" without recreating it.

        The shortcut already exists from the prior sync (created by
        ``_reconcile_phase_sync_games`` with ``tags["2"] = "Not Installed"``).
        On install we only need to: (a) flip that tag to ``""``,
        (b) write the games.map entry the launcher uses to resolve
        the exe, and (c) emit a state-change event so SyncService and
        the frontend cache update.

        Critically, we read the existing ``appid`` off the shortcut
        and reuse it for the games.map row. Regenerating with the
        exe path (the worker's old behaviour) would diverge from the
        launcher-anchored id sync wrote, leaving every appid-keyed
        lookup downstream broken.

        Matching has two passes: first by ``LaunchOptions`` (the
        cheap, exact-store path); then by ``AppName`` + Unifideck
        tag, which catches the cross-store-dedup case (user owns
        the title on Epic and GOG; dedup picked one for the
        shortcut; user installs the other). When the title-fallback
        hits, we rewrite the matched entry's ``LaunchOptions`` to
        point at the actually-installed store so the launcher can
        resolve the exe via games.map.

        Returns the existing app_id, or None if no shortcut matches.
        """
        await self._load_shortcuts()
        await self._load_games_map()

        target_launch = f"{store}:{store_game_id}"
        shortcuts_root = self._shortcuts.get("shortcuts") if isinstance(
            self._shortcuts, dict,
        ) else None
        if not isinstance(shortcuts_root, dict):
            logger.warning(
                "[ShortcutService] mark_installed %s — shortcuts.vdf empty",
                target_launch,
            )
            return None

        located: tuple[int | None, str | None] = (
            self._locate_installable_shortcut(
                shortcuts_root, target_launch, title,
            )
        )
        existing_app_id, prev_launch = located
        if existing_app_id is None:
            logger.warning(
                "[ShortcutService] mark_installed %s (title=%r) — no "
                "shortcut found (sync may not have run yet)",
                target_launch, title,
            )
            return None

        # If the matched shortcut belonged to a different store (the
        # cross-store-dedup case), drop the old games.map row so a
        # stale "epic:<id>" entry doesn't keep pointing at a
        # now-uninstalled binary.
        if prev_launch and prev_launch != target_launch:
            self._games_map.pop(prev_launch, None)
            logger.info(
                "[ShortcutService] mark_installed re-bound shortcut from "
                "%s to %s (cross-store install)",
                prev_launch, target_launch,
            )

        self._games_map[target_launch] = GameMapEntry(
            exe=exe_path, work_dir=install_path, app_id=existing_app_id,
        )

        await self._save_all()

        if self._bus:
            from unifideck.core.types.events import Events
            await self._bus.emit(
                Events.SHORTCUT_INSTALL_STATE_CHANGED,
                store=store,
                store_game_id=store_game_id,
                title=title,
                app_id=existing_app_id,
                installed=True,
                exe_path=exe_path,
                install_path=install_path,
                prev_store_game_id=(
                    prev_launch if prev_launch != target_launch else None
                ),
            )
        logger.info(
            "[ShortcutService] mark_installed %s → app_id=%d",
            target_launch, existing_app_id,
        )
        return existing_app_id

    def _locate_installable_shortcut(
        self: Any,
        shortcuts_root: dict[str, Any],
        target_launch: str,
        title: str,
    ) -> tuple[int | None, str | None]:
        """Find the shortcut to flip + rewrite LaunchOptions if needed.

        Two-pass match:
          1. ``LaunchOptions == target_launch`` — exact same store.
          2. ``AppName == title`` + Unifideck-tagged — the user
             installed a different store version of the same title.
             On hit, rewrite the entry's LaunchOptions (preserving
             user-added params like ``MANGOHUD=1``) so the launcher
             resolves the new games.map row.

        On either match we flip ``tags["2"]`` to the installed
        marker and return ``(appid, prev_launch_options)``.
        ``prev_launch_options`` is the old key needed to clean
        up the stale games.map row in the caller.
        """
        # Pass 1 — exact LaunchOptions match.
        for entry in shortcuts_root.values():
            if not isinstance(entry, dict):
                continue
            launch = entry.get("LaunchOptions", "")
            if not isinstance(launch, str):
                continue
            if get_full_id(launch) != target_launch:
                continue
            appid = entry.get("appid")
            if not isinstance(appid, int):
                continue
            self._mark_entry_installed(entry)
            return appid, target_launch

        # Pass 2 — AppName + UNIFIDECK_TAG fallback.
        for entry in shortcuts_root.values():
            if not isinstance(entry, dict):
                continue
            if entry.get("AppName") != title:
                continue
            if not _GamesMapMixin._entry_has_unifideck_tag(entry):
                continue
            appid = entry.get("appid")
            if not isinstance(appid, int):
                continue
            current_launch = entry.get("LaunchOptions", "")
            current_launch_str = (
                current_launch if isinstance(current_launch, str) else ""
            )
            entry["LaunchOptions"] = preserve_user_params(
                current_launch_str, target_launch,
            )
            self._mark_entry_installed(entry)
            return appid, get_full_id(current_launch_str)

        return None, None

    @staticmethod
    def _mark_entry_installed(entry: dict[str, Any]) -> None:
        """Set ``tags["2"] = ""`` (the installed marker)."""
        tags = entry.get("tags")
        if not isinstance(tags, dict):
            tags = {}
            entry["tags"] = tags
        tags["2"] = ""

    async def mark_uninstalled(
        self: Any,
        store: str,
        store_game_id: str,
        title: str = "",
    ) -> int | None:
        """Symmetric counterpart to :meth:`mark_installed`.

        Flips an existing shortcut back to "not installed" while
        preserving it in shortcuts.vdf — the user still owns the
        game, they just removed the bytes. The shortcut keeps its
        appid so the frontend cache and detail-page UI continue to
        recognise it. We additionally drop the games.map row since
        the launcher can no longer resolve an exe.

        ``title`` enables the same title-fallback as
        :meth:`mark_installed` for the cross-store case (the
        shortcut's LaunchOptions belong to a different store than
        the one we're uninstalling).

        Returns the existing app_id, or None if no shortcut matches.
        """
        await self._load_shortcuts()
        await self._load_games_map()

        target_launch = f"{store}:{store_game_id}"
        shortcuts_root = self._shortcuts.get("shortcuts") if isinstance(
            self._shortcuts, dict,
        ) else None
        if not isinstance(shortcuts_root, dict):
            logger.warning(
                "[ShortcutService] mark_uninstalled %s — shortcuts.vdf empty",
                target_launch,
            )
            return None

        existing_app_id: int | None = None
        for entry in shortcuts_root.values():
            if not isinstance(entry, dict):
                continue
            launch = entry.get("LaunchOptions", "")
            if not isinstance(launch, str):
                continue
            if get_full_id(launch) != target_launch:
                continue
            appid = entry.get("appid")
            if not isinstance(appid, int):
                continue
            existing_app_id = appid
            tags = entry.get("tags")
            if not isinstance(tags, dict):
                tags = {}
                entry["tags"] = tags
            tags["2"] = "Not Installed"
            break

        # Title fallback: shortcut belongs to a different store.
        if existing_app_id is None and title:
            for entry in shortcuts_root.values():
                if not isinstance(entry, dict):
                    continue
                if entry.get("AppName") != title:
                    continue
                if not _GamesMapMixin._entry_has_unifideck_tag(entry):
                    continue
                appid = entry.get("appid")
                if not isinstance(appid, int):
                    continue
                existing_app_id = appid
                tags = entry.get("tags")
                if not isinstance(tags, dict):
                    tags = {}
                    entry["tags"] = tags
                tags["2"] = "Not Installed"
                break

        if existing_app_id is None:
            logger.warning(
                "[ShortcutService] mark_uninstalled %s (title=%r) — no "
                "shortcut found",
                target_launch, title,
            )
            return None

        self._games_map.pop(target_launch, None)

        await self._save_all()

        if self._bus:
            from unifideck.core.types.events import Events
            await self._bus.emit(
                Events.SHORTCUT_INSTALL_STATE_CHANGED,
                store=store,
                store_game_id=store_game_id,
                title=title,
                app_id=existing_app_id,
                installed=False,
                exe_path="",
                install_path="",
                prev_store_game_id=None,
            )
        logger.info(
            "[ShortcutService] mark_uninstalled %s → app_id=%d",
            target_launch, existing_app_id,
        )
        return existing_app_id

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
