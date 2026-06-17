"""
Hide Ubisoft games that are owned on the native Steam library.

OP-55i (re-implementation) | py_modules/unifideck/stores/ubisoft/library/steam_filter.py

A Ubisoft title the user owns on Steam still shows up in UPC, but its
``uplay://`` shortcut is a dead end — the entitlement is bound to the
Steam copy, so the game can only launch from Steam, not the Ubisoft
launcher. Surfacing it as a Unifideck shortcut just produces a
non-launchable entry, so we hide it.

The original ``steam_filter.py`` was removed (commits 6c84e7e / 908d350)
for being flaky. This re-implementation is deliberately conservative to
avoid the two failure modes that almost certainly caused that:

* **No fuzzy matching.** Only an *exact* normalised-title equality hides
  a game — substring/fuzzy matching is the likeliest cause of
  false-positive hiding.
* **Never hide on incomplete data.** If the Steam title scan returns
  nothing (Steam not found, scanned too early at boot), filtering is
  skipped entirely — otherwise games flicker in and out across syncs.

Matching is unified on :func:`normalize_title_for_matching` (the shared
cross-store normaliser also used by ``steam.owned_games``), so both
sides of the comparison are normalised identically.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from unifideck.metadata.unifidb import normalize_title_for_matching

if TYPE_CHECKING:
    from unifideck.core.types import Game

logger = logging.getLogger(__name__)
# Steam ships these as appmanifest entries too; never treat them as games.
_NON_GAME_PREFIXES = (
    "proton",
    "steam linux runtime",
    "steamworks",
    "steamvr",
)


def load_steam_owned_titles() -> frozenset[str]:
    """Normalised titles of games on the native Steam library.

    Backed by :func:`unifideck.steam.owned_games.get_owned_titles` — the
    shared cross-store accessor, which reads ``appmanifest_*.acf`` and
    normalises with :func:`normalize_title_for_matching`. Returns an
    empty set when Steam can't be located or the scan fails; callers
    MUST treat empty as "don't filter", never "hide everything".

    Note: this currently reflects *installed* Steam games (the manifest
    set). Broadening to the full owned set (licensecache) is a future
    enhancement shared with the cross-source dedupe groundwork.
    """
    try:
        from unifideck.steam.owned_games import get_owned_titles
    except ImportError:
        logger.debug("[UbisoftSteamFilter] steam.owned_games unavailable")
        return frozenset()
    try:
        titles = get_owned_titles()
    except Exception as e:
        logger.debug("[UbisoftSteamFilter] Steam library scan failed: %s", e)
        return frozenset()
    return frozenset(
        title
        for title in titles
        if title and not title.startswith(_NON_GAME_PREFIXES)
    )


def apply_steam_owned_filter(
    games: list[Game],
    steam_titles: frozenset[str],
) -> tuple[list[Game], list[str]]:
    """Drop not-installed Ubisoft games whose title is owned on Steam.

    Returns ``(kept_games, hidden_titles)``. Installed games are always
    kept — we never hide a game the user actually installed through us.
    See the module docstring for the matching/safety rationale.
    """
    if not steam_titles:
        return games, []
    kept: list[Game] = []
    hidden: list[str] = []
    for game in games:
        norm = normalize_title_for_matching(game.title)
        if not game.installed and norm and norm in steam_titles:
            hidden.append(game.title)
            continue
        kept.append(game)
    if hidden:
        logger.info(
            "[UbisoftSteamFilter] hid %d Steam-owned title(s): %s",
            len(hidden),
            ", ".join(sorted(hidden)),
        )
    return kept, hidden
