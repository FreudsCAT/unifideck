"""Build the Battle.net library from client-local state.

py_modules/unifideck/stores/battlenet/library.py

Joins the three sources the Phase 0 spike identified::

    licences (CachedData.db)  ─┐
                               ├─> PUB catalog rules ─> playable programs
    game accounts (web/opt)   ─┘
                                        │
    aggregate.json + product.db ────────┴─> installed overlay

Both fact sources are required. Licences alone miss every free-to-play and
subscription title, because those match on ``game_account`` rather than
``license_id``; the web endpoint alone misses everything purchased.
Measured on one real account: licences gave 17 programs, licences plus game
accounts gave 22, and every one resolved to a name and an install uid.

The library is keyed on the **uid**, not the family code. A uid is stable
(``fenris`` has never changed) while Blizzard renames families — Diablo IV
went ``D4`` -> ``Fen`` in 2026 — and the Steam app id is derived from
``store_game_id``, so a re-key would silently orphan the user's shortcut,
playtime, categories and artwork.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from unifideck.core.types.domain import Game

from .ownership import (
    AccountFacts,
    InstalledGame,
    MergedCatalog,
    evaluate_catalog,
    read_installed,
    read_licences,
)
from .ownership.pub_catalog import CatalogEntry
from .product_db import read_product_db

logger = logging.getLogger(__name__)

STORE_NAME = "battlenet"


def _tags(entry: CatalogEntry | None, free_to_play: bool) -> list[str]:
    tags: list[str] = []
    if free_to_play:
        tags.append("free_to_play")
    for status in entry.handheld_status if entry else ():
        # 'handheld_optimized' / 'handheld_compatible' / 'handheld_unsupported'
        tags.append(status)
    return tags


def _game_from(
    program: str,
    entry: CatalogEntry | None,
    catalog: MergedCatalog,
    installed: InstalledGame | None,
    *,
    free_to_play: bool,
    launcher_path: str,
    uid: str | None = None,
) -> Game | None:
    # An explicit uid wins: an installed game the catalog does not describe
    # still has one, and deriving it from a missing entry would drop the
    # game the fallback exists to preserve.
    uid = uid or (entry.uid_for() if entry else None)
    if not uid:
        # No uid means nothing to install or launch. Surfacing it would put
        # a dead tile in the user's library.
        logger.info("[Battlenet] skipping %s — catalog has no install uid", program)
        return None

    name = catalog.display_name(program) or (installed.name if installed else None) or program
    from unifideck.services.shortcut.games_map import generate_app_id

    return Game(
        app_id=generate_app_id(launcher_path, f"{STORE_NAME}:{uid}"),
        store=STORE_NAME,
        store_game_id=uid,
        title=name,
        installed=bool(installed and installed.is_ready),
        install_path=installed.host_install_path if installed else None,
        exe_path=installed.host_exe_path if installed else None,
        size_bytes=(installed.total_bytes or 0) if installed else 0,
        tags=_tags(entry, free_to_play),
        icon_url=installed.logo_art_url if installed else None,
        hero_url=installed.box_art_url if installed else None,
        metadata={
            "family": program,
            "title_id": entry.title_id if entry else None,
            "version": installed.version if installed else None,
            "last_played_ms": installed.last_played_ms if installed else None,
        },
    )


def family_updates(games: list[Game]) -> dict[str, dict[str, Any]]:
    """``uid -> {"family": …}`` for every game whose family the catalog knew.

    The family code is the ``--exec`` argument the client needs and it lives
    only here, in the catalog join — the launcher runs out-of-process and
    cannot recompute it. Persisting it at sync is what makes a game
    launchable *before* it is installed, and is the only writer that sees
    every title rather than just the one being installed.
    """
    updates: dict[str, dict[str, Any]] = {}
    for game in games:
        family = game.metadata.get("family") if game.metadata else None
        if isinstance(family, str) and family and game.store_game_id:
            updates[game.store_game_id] = {"family": family}
    return updates


def record_families(id_map: Any, games: list[Game]) -> int:
    """Persist each title's ``--exec`` family code. Returns how many changed.

    Best-effort by contract: an unwritable id map must not fail a library
    read, because an empty library is a far worse outcome than a launch that
    later reports a missing family.
    """
    try:
        return int(id_map.merge_many(family_updates(games)))
    except Exception:
        logger.exception("[Battlenet] could not record family codes")
        return 0


def family_from_catalog(catalog: MergedCatalog, uid: str) -> str | None:
    """The program id (family) whose install uid is ``uid``, or None.

    The catalog maps family -> uid, so going the other way means scanning.
    Only used on the install path, where a title may not have been through a
    sync yet; :func:`record_families` covers the whole library at once.
    """
    for entry in catalog.entries.values():
        if entry.uid_for() == uid:
            return entry.program_id
    return None


def _index_by_uid(installed: dict[str, InstalledGame]) -> dict[str, InstalledGame]:
    """Re-key install state on uid.

    ``aggregate.json`` and ``product.db`` are keyed on the product CODE
    (``hsb``) while the catalog addresses titles by uid (``hs_beta``). The
    uid is the only field common to both, so the join has to go through it —
    matching on code silently reports every installed game as not installed.
    """
    by_uid: dict[str, InstalledGame] = {}
    for game in installed.values():
        if game.uid:
            by_uid[game.uid] = game
    return by_uid


def install_state_by_uid(drive_c: Path, prefix: Path) -> dict[str, InstalledGame]:
    """Install state for one prefix, keyed the way the rest of the code asks.

    The install watcher needs to ask about *one* uid — "is the title the user
    pressed Install on ready yet" — and must not re-derive the code→uid join
    to do it. Getting that join wrong reports every installed game as not
    installed, which is the regression ``_index_by_uid`` exists to prevent.
    """
    return _index_by_uid(read_install_state(drive_c, prefix))


def build_library(
    catalog: MergedCatalog,
    facts: AccountFacts,
    installed: dict[str, InstalledGame],
    *,
    launcher_path: str,
) -> list[Game]:
    """Join ownership, catalog metadata and install state into Games."""
    granted = evaluate_catalog(catalog.program_configurations, facts)
    by_uid = _index_by_uid(installed)
    games = _granted_games(granted, catalog, by_uid, launcher_path)
    seen = {g.store_game_id for g in games}
    games.extend(_orphan_installed(installed, catalog, seen, launcher_path))
    return games


def _granted_games(
    granted: dict[str, frozenset[Any]],
    catalog: MergedCatalog,
    by_uid: dict[str, InstalledGame],
    launcher_path: str,
) -> list[Game]:
    games: list[Game] = []
    for program, products in granted.items():
        entry = catalog.entry_for(program)
        uid = entry.uid_for() if entry else None
        game = _game_from(
            program,
            entry,
            catalog,
            by_uid.get(uid) if uid else None,
            free_to_play=any(p.is_free_to_play for p in products),
            launcher_path=launcher_path,
        )
        if game is not None:
            games.append(game)
    return games


def _orphan_installed(
    installed: dict[str, InstalledGame],
    catalog: MergedCatalog,
    seen_uids: set[str],
    launcher_path: str,
) -> list[Game]:
    """Installed titles the rules did not grant.

    They must not vanish: an ownership hiccup would otherwise take the
    user's installed game — and its Steam shortcut — with it.
    """
    games: list[Game] = []
    for code, state in installed.items():
        if not state.is_ready:
            continue
        entry = catalog.entry_for(code)
        uid = state.uid or (entry.uid_for() if entry else None) or code
        if uid in seen_uids:
            continue
        logger.info(
            "[Battlenet] %s is installed but not granted by the rules — "
            "keeping it in the library", code,
        )
        game = _game_from(
            entry.program_id if entry else code,
            entry, catalog, state,
            free_to_play=False, launcher_path=launcher_path, uid=uid,
        )
        if game is not None:
            games.append(game)
    return games


def read_account_facts(drive_c: Path, game_account_programs: frozenset[str]) -> AccountFacts:
    """Assemble the account facts the catalog rules are evaluated against."""
    licences = read_licences(drive_c)
    return AccountFacts(
        licence_ids=licences.licence_ids,
        game_account_programs=game_account_programs,
    )


def read_install_state(drive_c: Path, prefix: Path) -> dict[str, InstalledGame]:
    """Installed state for one prefix, with host paths resolved."""
    return read_installed(drive_c, read_product_db(drive_c), prefix=prefix)
