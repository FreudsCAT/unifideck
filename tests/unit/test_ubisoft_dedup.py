"""Unit tests for Ubisoft parent/edition DLC dedup in ``_GameBuilder``.

Ported from staging's ``known_base_names`` / ``all_db_names`` ownership
dedup (staging ``ubisoft.py`` ~L2180-2340). The refactor seeds the base
set from the kept configs (no GraphQL) and from the community game-ID DB.

Three behaviours are covered:

1. ``"Parent - DLC"`` collapses when ``Parent`` is an owned base game
   or appears in the community DB.
2. ``"Parent: Subtitle"`` only collapses against the owned base set, so
   standalone subtitled titles (Prince of Persia) survive.
3. Edition variants ("Gold Edition") collapse onto the plain base title.
"""
from __future__ import annotations

from typing import Any

import pytest

from unifideck.stores.ubisoft.id_map import UbisoftIdMap
from unifideck.stores.ubisoft.library.game_builder import _GameBuilder
from unifideck.stores.ubisoft.parser import GameConfig


def _norm(name: str) -> str:
    """Reuse the production normaliser so tests track its behaviour."""
    return UbisoftIdMap._normalize_for_matching(name)


class _IdMap:
    """id_map double exposing only what ``_GameBuilder`` touches."""

    def __init__(self) -> None:
        self.bulk: dict[str, dict[str, Any]] = {}

    def normalize_for_matching(self, name: str) -> str:
        return _norm(name)

    def update_bulk(self, mapping: dict[str, dict[str, Any]]) -> None:
        self.bulk.update(mapping)


def _cfg(install_id: int, space_id: str, name: str) -> GameConfig:
    c = GameConfig()
    c.install_id = install_id
    c.launch_id = install_id
    c.space_id = space_id
    c.name = name
    return c


def _builder() -> _GameBuilder:
    return _GameBuilder(config=object(), id_map=_IdMap())


def _titles(games: list[Any]) -> set[str]:
    return {g.title for g in games}


def test_named_expansion_with_dash_is_dropped():
    """"Base - Expansion" collapses when the base game is owned."""
    base = _cfg(1, "s1", "Assassin's Creed Valhalla")
    dlc = _cfg(2, "s2", "Assassin's Creed Valhalla - Dawn of Ragnarok")
    games = _builder().build_games_from_configs(
        [base, dlc], installed={},
    )
    assert _titles(games) == {"Assassin's Creed Valhalla"}


def test_dash_parent_from_db_only_is_dropped():
    """A " - " DLC collapses when the parent is only in the community DB."""
    dlc = _cfg(2, "s2", "Watch Dogs Legion - Season Pass Extra")
    db_names = {_norm("Watch Dogs Legion")}
    games = _builder().build_games_from_configs(
        [dlc], installed={}, db_names=db_names,
    )
    # "season pass" also trips the keyword filter, so prove the parent
    # path with a keyword-clean subtitle:
    dlc2 = _cfg(3, "s3", "Watch Dogs Legion - Bloodline Story")
    games2 = _builder().build_games_from_configs(
        [dlc2], installed={}, db_names=db_names,
    )
    assert games == []
    assert games2 == []


def test_colon_standalone_title_survives():
    """"Parent: Subtitle" is kept when no base "Parent" is owned."""
    game = _cfg(1, "s1", "Prince of Persia: The Sands of Time")
    db_names = {_norm("Prince of Persia")}  # DB has it; must NOT matter
    games = _builder().build_games_from_configs(
        [game], installed={}, db_names=db_names,
    )
    assert _titles(games) == {"Prince of Persia: The Sands of Time"}


def test_colon_sequel_not_dropped_even_when_prefix_owned():
    """A standalone "Franchise: Subtitle" survives even if the prefix is owned.

    This is the deliberate divergence from staging: colon parent-matching
    is unsafe without GraphQL pre-filtering, so owning *Watch Dogs* must
    NOT delete the standalone *Watch Dogs: Legion*.
    """
    base = _cfg(1, "s1", "Watch Dogs")
    sequel = _cfg(2, "s2", "Watch Dogs: Legion")
    games = _builder().build_games_from_configs(
        [base, sequel], installed={},
    )
    assert _titles(games) == {"Watch Dogs", "Watch Dogs: Legion"}


def test_edition_variant_collapses_onto_base():
    """"X Gold Edition" collapses onto plain "X" (plain wins)."""
    base = _cfg(1, "s1", "Far Cry 6")
    gold = _cfg(2, "s2", "Far Cry 6 Gold Edition")
    games = _builder().build_games_from_configs(
        [base, gold], installed={},
    )
    assert _titles(games) == {"Far Cry 6"}


def test_lone_edition_is_kept():
    """An edition with no plain base present is still shown."""
    gold = _cfg(1, "s1", "Far Cry 6 Gold Edition")
    games = _builder().build_games_from_configs([gold], installed={})
    assert _titles(games) == {"Far Cry 6 Gold Edition"}


def test_distinct_games_not_collapsed():
    """Sequels / distinct titles are never merged."""
    a = _cfg(1, "s1", "Assassin's Creed")
    b = _cfg(2, "s2", "Assassin's Creed II")
    games = _builder().build_games_from_configs([a, b], installed={})
    assert _titles(games) == {"Assassin's Creed", "Assassin's Creed II"}


@pytest.mark.parametrize(
    ("parent", "exact", "substr", "expected"),
    [
        ("far cry", {"far cry"}, set(), True),  # exact
        ("tom", {"tommy"}, {"tommy"}, False),  # too short for substring
        (
            "rainbow six siege",
            set(),
            {"tom clancys rainbow six siege"},
            True,
        ),  # substring fallback
        ("", {"far cry"}, {"far cry"}, False),  # empty parent
    ],
)
def test_parent_matches(parent, exact, substr, expected):
    assert _GameBuilder._parent_matches(parent, exact, substr) is expected
