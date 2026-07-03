"""Tests for the cross-source dedupe groundwork (disabled by default).

Exercises the pure ``collapse_duplicates`` function: it is wired into
``SyncService._aggregate_results`` but only runs when
``dedup.cross_store_enabled`` is set, so these tests pin the collapse
semantics independently of the (default-off) seam.
"""
from __future__ import annotations

from unifideck.core.cross_source_dedupe import collapse_duplicates
from unifideck.core.types import Game

_TRACKED = ("epic", "gog", "amazon", "ubisoft")


def _g(store: str, title: str, *, installed: bool = False) -> Game:
    return Game(
        app_id=0,
        store=store,
        store_game_id=f"{store}-{title}".lower().replace(" ", "-"),
        title=title,
        installed=installed,
    )


def test_no_tracked_stores_is_noop():
    games = [_g("epic", "Game A"), _g("gog", "Game A")]
    assert collapse_duplicates(games, tracked_stores=()) == games


def test_unique_titles_pass_through():
    games = [_g("epic", "Game A"), _g("gog", "Game B")]
    out = collapse_duplicates(games, tracked_stores=_TRACKED)
    assert [g.title for g in out] == ["Game A", "Game B"]


def test_duplicate_collapses_by_precedence():
    """Same title on epic + gog → epic wins (earlier in precedence)."""
    games = [_g("gog", "Far Cry 6"), _g("epic", "Far Cry 6")]
    out = collapse_duplicates(games, tracked_stores=_TRACKED)
    assert len(out) == 1
    assert out[0].store == "epic"


def test_installed_beats_precedence():
    """An installed copy wins even from a lower-precedence store."""
    games = [
        _g("epic", "Anno 1800"),  # higher precedence, not installed
        _g("ubisoft", "Anno 1800", installed=True),
    ]
    out = collapse_duplicates(games, tracked_stores=_TRACKED)
    assert len(out) == 1
    assert out[0].store == "ubisoft"


def test_untracked_store_never_collapsed():
    """Microsoft (untracked) entries always survive, even as duplicates."""
    games = [
        _g("epic", "Sea of Thieves"),
        _g("microsoft", "Sea of Thieves"),
    ]
    out = collapse_duplicates(games, tracked_stores=_TRACKED)
    stores = {g.store for g in out}
    assert stores == {"epic", "microsoft"}


def test_order_preserved_by_first_appearance():
    games = [
        _g("gog", "Game A"),
        _g("epic", "Game B"),
        _g("epic", "Game A"),  # collapses onto first A slot, epic wins it
    ]
    out = collapse_duplicates(games, tracked_stores=_TRACKED)
    assert [g.title for g in out] == ["Game A", "Game B"]
    a = next(g for g in out if g.title == "Game A")
    assert a.store == "epic"


def test_title_normalisation_matches_across_punctuation():
    games = [
        _g("epic", "Tom Clancy's Rainbow Six® Siege"),
        _g("ubisoft", "Tom Clancys Rainbow Six Siege"),
    ]
    out = collapse_duplicates(games, tracked_stores=_TRACKED)
    assert len(out) == 1
    assert out[0].store == "epic"
