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
        # Auth shortcut sentinel — never delete.
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
        # When ``valid_stores`` is specified, leave shortcuts for
        # offline / disabled stores untouched.
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
        at the end. Returns ``{added, removed, kept}`` counts.

        Implementation breakdown:
          1. Drop ``games.map`` keys not in the new library.
          2. For each game: create-or-keep the ``shortcuts.vdf``
             entry, refresh ``games.map``.
          3. Drop ``shortcuts.vdf`` entries tagged as Unifideck-
             managed but absent from the new library.

        Refactor history (2026-05-14): was at McCabe CC=16 — the
        three phases were inlined with their own loops, set
        builds and counter increments. Pulled each phase into a
        dedicated helper so this top-level method now reads as a
        manifest of the algorithm itself: phase 1, phase 2,
        phase 3, save.
        """
        await self._load_shortcuts()
        await self._load_games_map()

        # Drift fix (lot 12c): ``Game`` doesn't have ``id`` /
        # ``launch_path`` attributes — the canonical fields are
        # ``store_game_id`` and ``exe_path``. The previous shape
        # raised AttributeError at runtime, but the set comprehensions
        # below were never exercised by the test suite. mypy strict
        # surfaced the drift.
        from .registry import load_registry, save_registry

        valid_keys = {f"{g.store}:{g.store_game_id}" for g in games}
        # AppID is keyed on the *launcher* path, not the per-game
        # exe — anchoring on the launcher keeps the id stable
        # across install / uninstall transitions. Falls back to
        # ``g.app_id`` (populated by ``SyncService._populate_app_ids``)
        # so we never miss an entry.
        launcher = getattr(self, "_launcher_path", "") or ""
        valid_app_ids = {
            g.app_id or generate_app_id(launcher, g.title) for g in games
        }
        # Only sweep shortcuts whose store prefix is present in
        # ``games``. If the user is logged out of Epic this run,
        # the games list won't contain any epic: entries, so we
        # leave Epic shortcuts untouched (staging behaviour).
        valid_stores = {g.store for g in games}

        # Load the persistent registry once for the whole sweep —
        # passed into phase 2 for reclamation, written back after.
        registry = load_registry()
        registry_dirty = False

        # Phase 1 — prune the games.map entries that no longer
        # correspond to a game in the new library.
        removed = self._reconcile_phase_prune_map(valid_keys)

        # Phase 2 — ensure each current game has both a games.map
        # entry and a shortcuts.vdf entry.
        self._shortcuts = self._ensure_shortcuts_root(self._shortcuts)
        shortcuts_dict = self._shortcuts["shortcuts"]
        added, kept, reclaimed = self._reconcile_phase_sync_games(
            games, shortcuts_dict, registry,
        )
        if added > 0 or reclaimed > 0:
            registry_dirty = True

        # Phase 3 — drop shortcuts.vdf entries that look
        # Unifideck-managed (by LaunchOptions or legacy tag) but
        # whose app_id is no longer in the current library.
        # Scoped to ``valid_stores`` so we never touch shortcuts
        # belonging to currently-offline stores.
        removed += self._reconcile_phase_drop_stale(
            shortcuts_dict, valid_app_ids, valid_stores,
        )

        if added > 0 or removed > 0 or reclaimed > 0:
            await self._save_all()
        if registry_dirty:
            save_registry(registry)

        logger.info(
            "[ShortcutService] reconcile: %d games → added=%d kept=%d removed=%d reclaimed=%d",
            len(games), added, kept, removed, reclaimed,
        )
        return {
            "added": added, "removed": removed,
            "kept": kept, "reclaimed": reclaimed,
        }

    # ─────────────────────────────────────────────────────────────
    # Reconcile phases — extracted to flatten the CC of reconcile.
    # ─────────────────────────────────────────────────────────────

    def _reconcile_phase_prune_map(
        self: Any, valid_keys: set[str],
    ) -> int:
        """Phase 1: drop ``_games_map`` keys absent from ``valid_keys``.

        Returns the number of entries removed.
        """
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
        """Phase 2: ensure each game has both a map entry and a VDF entry.

        Returns ``(added, kept, reclaimed)`` — number of new
        shortcuts appended, left in place, and rescued from a
        previously-orphaned ``shortcuts.vdf`` row (Steam mangled
        the LaunchOptions, but we recognised the AppID from the
        persistent registry).  The ``games.map`` is rewritten
        unconditionally so a metadata-only refresh (e.g.
        ``work_dir`` moved) propagates without churning the VDF.
        """
        from .registry import get_registered_appid, register

        added = 0
        kept = 0
        reclaimed = 0
        launcher = getattr(self, "_launcher_path", "") or ""
        for game in games:
            # ``store_game_id`` is the store-native id; index the
            # games.map by ``store:store_game_id`` so the launcher
            # can look up the install dir / exe at runtime.
            key = f"{game.store}:{game.store_game_id}"
            launch_options = key
            exe = game.exe_path or ""
            # Prefer the AppID populated by SyncService (stable
            # across install state). Fall back to deriving one
            # from launcher_path + title so the mixin still works
            # if used without SyncService.
            app_id = game.app_id or generate_app_id(launcher, game.title)
            self._games_map[key] = GameMapEntry(
                exe=exe, work_dir=game.install_path or "",
            )
            # Reclamation: if we have a registered AppID for this
            # game from a prior install and that AppID exists in
            # shortcuts.vdf as an orphan (Steam wiped its tags or
            # LaunchOptions), rewrite the orphan in-place. Keeps
            # the AppID stable so Steam's cached artwork lines up.
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
        """Rewrite ``entry`` in place so it points at ``game`` again.

        Preserves the existing ``appid`` (so Steam's cached
        artwork survives) while restoring the canonical
        Unifideck shape: launcher Exe, ``store:game_id`` launch
        options, Unifideck tags, current title.

        Any user-appended LaunchOptions params (``LSFG=1``,
        ``%command%``, MangoHud, …) get stitched back in via
        :func:`preserve_user_params`.
        """
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
        """Phase 3: delete Unifideck-managed shortcuts no longer needed.

        Only entries with our tag get deleted — user-created
        shortcuts are preserved even if they happen to share an
        appid (very unlikely, but the symmetry with ``add_game``
        and ``remove_game`` matters). Auth shortcuts (tagged
        ``auth-*``) are also preserved here ; their lifecycle is
        managed by ``services/shortcut/shortcut.py``.

        Returns the number of entries dropped.
        """
        keys_to_delete = [
            vdf_key for vdf_key, entry in shortcuts_dict.items()
            if self._is_stale_managed_shortcut(
                entry, valid_app_ids, valid_stores,
            )
        ]
        for key in keys_to_delete:
            del shortcuts_dict[key]
        return len(keys_to_delete)

    def _build_shortcut_entry(self: Any, game: Game, app_id: int) -> dict[str, Any]:
        """Construct a shortcuts.vdf entry dict for ``game``.

        Every Unifideck-managed shortcut points its ``Exe`` at the
        plugin's ``bin/unifideck-launcher`` script and stores the
        ``"<store>:<store_game_id>"`` token in ``LaunchOptions``.
        When the user clicks the Steam tile, the launcher reads
        ``LaunchOptions``, looks up the install dir in
        ``games.map``, and either runs the game (installed) or
        kicks off the install flow (not installed). Per staging
        UX — shortcuts exist for the user's entire library, not
        just installed games.

        Anchoring on the launcher (not on the per-game exe) is
        what makes the shortcut AppID stable across install /
        uninstall transitions — Steam keys covers, playtime,
        compat tool settings, etc. on the AppID, and changing
        the AppID throws all of that away.
        """
        launcher = getattr(self, "_launcher_path", "") or ""
        exe_quoted = f'"{launcher}"' if launcher else '""'
        # StartDir is the working directory Steam cd's into
        # before exec'ing. The launcher doesn't care so the
        # install_path is a fine default; empty when unknown.
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
                # Staging tagged uninstalled games "Not Installed"
                # so users can filter on it. Empty string for
                # installed games keeps the tag dict shape stable.
                "2": "" if game.installed else "Not Installed",
            },
        }
