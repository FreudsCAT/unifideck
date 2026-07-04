"""
Cross-source same-game collapse — GROUNDWORK, disabled by default.

py_modules/unifideck/core/cross_source_dedupe.py

The eventual "one shortcut per game" feature. **Today, and by default,
each store's copy of a title is a distinct shortcut** — the store-scoped
``generate_app_id`` identity makes that the intended behaviour, and an
earlier cross-store dedup pass was deliberately removed (see
``core/sync_results_mixin`` history). This module is the *opt-in* seam to
collapse a title owned on several Unifideck stores down to a single
shortcut, plus the canonical key the future Steam-native dedupe will
share.

Wiring: ``SyncService._aggregate_results`` calls :func:`collapse_duplicates`
only when ``dedup.cross_store_enabled`` is true (default **false**).
Precedence comes from ``dedup.tracked_stores`` — Microsoft is deliberately
excluded there so Game Pass / xCloud entries are never collapsed away.

The display-layer companion — a per-user "hide duplicates" toggle that
collapses in the UI rather than at sync time — belongs in
``src/lib/library-filters/``. Both must key on the same canonical title
(:func:`normalize_title_for_matching`) so backend and frontend agree on
what "the same game" means.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from unifideck.metadata.unifidb import normalize_title_for_matching

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .types import Game


def collapse_duplicates(
    games: Sequence[Game],
    *,
    tracked_stores: Sequence[str],
    prefer_installed: bool = True,
) -> list[Game]:
    """Collapse cross-store duplicates of the same title to one ``Game``.

    A title (keyed by :func:`normalize_title_for_matching`) owned on more
    than one *tracked* store keeps a single winner; games on untracked
    stores (e.g. ``microsoft``) and unique titles always pass through.

    Winner selection, in order:

    1. installed beats not-installed (when ``prefer_installed``);
    2. earlier position in ``tracked_stores`` wins;
    3. first appearance wins (stable).

    First-appearance order of the surviving games is preserved. An empty
    ``tracked_stores`` is a no-op (returns the input as a list).
    """
    games = list(games)
    if not tracked_stores:
        return games
    precedence = {store: index for index, store in enumerate(tracked_stores)}

    def _key(game: Game) -> str | None:
        if game.store not in precedence:
            return None
        norm = normalize_title_for_matching(game.title)
        return norm or None

    winners: dict[str, Game] = {}
    for game in games:
        key = _key(game)
        if key is None:
            continue
        incumbent = winners.get(key)
        if incumbent is None or _beats(game, incumbent, precedence, prefer_installed):
            winners[key] = game

    result: list[Game] = []
    emitted: set[str] = set()
    for game in games:
        key = _key(game)
        if key is None:
            result.append(game)
            continue
        if key in emitted:
            continue
        result.append(winners[key])
        emitted.add(key)
    return result


def _beats(
    candidate: Game,
    incumbent: Game,
    precedence: dict[str, int],
    prefer_installed: bool,
) -> bool:
    """True if ``candidate`` should displace ``incumbent`` as the winner."""
    if prefer_installed and candidate.installed != incumbent.installed:
        return candidate.installed
    return precedence[candidate.store] < precedence[incumbent.store]
