"""Cross-store deduplication — same game across multiple stores.

OP-08h | py_modules/unifideck/core/cross_store_dedup.py

Many users own the same game on multiple stores (claimed
on Epic free weekly, bought on Steam earlier, included in
Game Pass). The unified library should show each title
exactly once, picking the "best" copy.

Algorithm:

1. **Steam filter (optional)** — if the caller passes
   ``steam_owned_titles``, any matching title in a tracked
   store is dropped immediately. Rationale: if the user
   already owns it on Steam, Unifideck shouldn't try to add
   a second entry.
2. **Cross-store dedup** — iterate the tracked stores in
   the order given. For each game:
   * If the normalised title isn't yet claimed, take it.
   * If it's claimed but the current copy is **installed**
     and the existing claim wasn't, the current copy wins
     and evicts the prior one.
   * Otherwise the current copy is dropped.

The "installed wins" rule means a user with the game on
Epic (uninstalled) and GOG (installed) sees the GOG entry,
which can launch.

Returns the deduplicated library + a ``dropped`` count map
for diagnostics / metrics.

Title matching uses
``unifideck.metadata.unifidb.normalize_title_for_matching``
which strips trademarks, edition suffixes, and punctuation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..metadata.unifidb import normalize_title_for_matching

if TYPE_CHECKING:
    from .types import Game

logger = logging.getLogger(__name__)


def deduplicate_libraries(
    libraries: dict[str, list[Game]],
    *,
    tracked_stores: tuple[str, ...] | list[str],
    steam_owned_titles: frozenset[str] | None = None,
) -> tuple[dict[str, list[Game]], dict[str, int]]:
    """Deduplicate ``libraries`` across the tracked stores.

    Top-level orchestration:

    1. Optionally pre-filter against Steam's owned titles
       set (drops titles the user already owns natively).
    2. Copy untracked stores through verbatim (they're
       included in the output but not subject to dedup).
    3. Walk the tracked stores in user-supplied order,
       claiming titles as they appear.

    Logs at INFO when at least one game was dropped, so
    operators see the dedup effect in plugin logs without
    enabling DEBUG.

    Args:
        libraries: mapping ``store_name → [Game, ...]``.
        tracked_stores: stores subject to cross-store
            dedup, in priority order (earlier wins on
            equal-tie).
        steam_owned_titles: optional pre-normalised set
            of Steam titles to filter against.

    Returns:
        Tuple ``(deduped_libraries, dropped_counts)``:

        * ``deduped_libraries`` — same shape as input;
        * ``dropped_counts`` — mapping ``store → drop
          count`` for the stores that lost entries.
    """
    if not libraries:
        return {}, {}
    tracked_set: frozenset[str] = frozenset(tracked_stores)
    deduped: dict[str, list[Game]] = {}
    dropped: dict[str, int] = {}
    if steam_owned_titles:
        libraries = _filter_against_steam(
            libraries,
            steam_owned_titles,
            tracked_set,
            dropped,
        )
    for store_name, games in libraries.items():
        if store_name not in tracked_set:
            deduped[store_name] = list(games)
    claimed: dict[str, _ClaimedEntry] = {}
    for store_name in _ordered_store_names(libraries, tracked_stores):
        kept, dropped_count = _dedup_store_games(
            store_name,
            libraries[store_name],
            deduped,
            claimed,
            dropped,
        )
        deduped[store_name] = kept
        if dropped_count:
            dropped[store_name] = dropped.get(store_name, 0) + dropped_count
    total = sum(dropped.values())
    if total:
        logger.info(
            "[cross_store_dedup] removed %d duplicate(s) across %d store(s)",
            total,
            len(dropped),
        )
    return deduped, dropped


class _ClaimedEntry:
    """Bookkeeping record for one claimed title.

    ``__slots__`` to keep memory tight when the claims
    dict has thousands of entries (large libraries).

    Attributes:
        store: which store currently owns the claim.
        installed: whether the claimed copy is installed
            (drives the "installed wins" eviction rule).
    """

    __slots__ = ("store", "installed")

    def __init__(self, *, store: str, installed: bool) -> None:
        """Initialise the bookkeeping record.

        Args:
            store: claiming store name.
            installed: install state of the claiming copy.
        """
        self.store = store
        self.installed = installed


def _filter_against_steam(
    libraries: dict[str, list[Game]],
    steam_owned: frozenset[str],
    tracked: frozenset[str],
    dropped: dict[str, int],
) -> dict[str, list[Game]]:
    """Drop titles already owned on Steam from tracked-store libraries.

    Untracked stores pass through untouched. For each
    tracked store, walks every game and checks whether
    its normalised title is in ``steam_owned``; if so,
    skip + bump the drop counter for that store.

    The drop counter is shared with the main
    ``deduplicate_libraries`` dict (mutated in place) so
    final summing is accurate.

    Args:
        libraries: full input mapping.
        steam_owned: normalised Steam-owned title set.
        tracked: tracked-stores set (membership lookup).
        dropped: shared drop-counter dict (mutated).

    Returns:
        Filtered mapping (same shape as input).
    """
    filtered: dict[str, list[Game]] = {}
    for store_name, games in libraries.items():
        if store_name not in tracked:
            filtered[store_name] = list(games)
            continue
        kept: list[Game] = []
        dropped_here = 0
        for game in games:
            key = normalize_title_for_matching(game.title)
            if key and key in steam_owned:
                dropped_here += 1
                continue
            kept.append(game)
        filtered[store_name] = kept
        if dropped_here:
            dropped[store_name] = dropped.get(store_name, 0) + dropped_here
    return filtered


def _dedup_store_games(
    store_name: str,
    games: list[Game],
    deduped: dict[str, list[Game]],
    claimed: dict[str, _ClaimedEntry],
    dropped: dict[str, int],
) -> tuple[list[Game], int]:
    """Walk one store's games, claiming titles and possibly evicting earlier wins.

    Per-game logic:

    * Title can't be normalised (empty result) → keep
      unchanged. Defensive: pathological titles
      (whitespace-only after normalisation) shouldn't be
      dropped accidentally.
    * Title not yet claimed → claim it, keep the game.
    * Title claimed and current beats existing
      (``_current_wins``) → evict the existing entry from
      its store's list, update the claim, keep the current
      game.
    * Otherwise → drop the current game.

    Args:
        store_name: store being processed.
        games: list of games for this store.
        deduped: output mapping (mutated when an eviction
            happens — the existing entry needs to be
            removed from the prior store's list).
        claimed: shared claims dict (mutated).
        dropped: shared drop-counter dict (mutated on
            eviction).

    Returns:
        Tuple ``(kept_games, drop_count_for_this_store)``.
    """
    kept: list[Game] = []
    dropped_here = 0
    for game in games:
        key = normalize_title_for_matching(game.title)
        if not key:
            kept.append(game)
            continue
        existing = claimed.get(key)
        if existing is None:
            claimed[key] = _ClaimedEntry(
                store=store_name,
                installed=game.installed,
            )
            kept.append(game)
            continue
        if _current_wins(game, existing):
            _evict_from_store(deduped, existing.store, key)
            dropped[existing.store] = dropped.get(existing.store, 0) + 1
            claimed[key] = _ClaimedEntry(
                store=store_name,
                installed=True,
            )
            kept.append(game)
        else:
            dropped_here += 1
    return kept, dropped_here


def _current_wins(game: Game, existing: _ClaimedEntry) -> bool:
    """Return True iff the current copy should evict the existing claim.

    The single rule: current is **installed**, existing
    was not. Other criteria (subscription vs owned, etc.)
    aren't considered here — could be added if needed,
    but the simple rule covers the common case where the
    user actively has the game running from one specific
    store.

    Args:
        game: current candidate.
        existing: prior claim.

    Returns:
        True if eviction should happen.
    """
    return game.installed and not existing.installed


def _ordered_store_names(
    libraries: dict[str, list[Game]],
    tracked_stores: tuple[str, ...] | list[str],
) -> list[str]:
    """Return tracked store names present in libraries, in user-supplied order.

    The order matters: earlier stores get first claim on
    any contested title, so the caller's
    ``tracked_stores`` order is effectively a per-title
    priority list (overridden only by the "installed
    wins" rule).

    Args:
        libraries: input mapping.
        tracked_stores: ordered iterable from the caller.

    Returns:
        Filtered + ordered list of store names.
    """
    return [name for name in tracked_stores if name in libraries]


def _evict_from_store(
    deduped: dict[str, list[Game]],
    store_name: str,
    title_key: str,
) -> None:
    """Remove the first game in ``store_name``'s list matching ``title_key``.

    Linear scan + ``del`` — O(N) per eviction. Acceptable
    because evictions are rare (only when a tracked store
    later in the priority list has an installed version
    of a title the earlier store had uninstalled).

    Silently returns on a missing store / empty list —
    the eviction is opportunistic, not mandatory.

    Args:
        deduped: output mapping (mutated).
        store_name: store to evict from.
        title_key: normalised title to look for.
    """
    games = deduped.get(store_name)
    if not games:
        return
    for idx, game in enumerate(games):
        if normalize_title_for_matching(game.title) == title_key:
            del games[idx]
            return
