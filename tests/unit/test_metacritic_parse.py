"""Metacritic composer parse: was dead, now validated.

The composer payload nests the game at ``components[*].data.item`` with
``item.type == "game-title"`` — component objects carry no ``type``. The
old parser looked for a component ``type == "gameInfo"`` that never exists,
so EVERY lookup returned None and the Metacritic backfill was silently
dead. The repaired parser also validates the landed page's title via
``titles_match`` so a slug variant / redirect can't attach a wrong game's
score (the whole library is triaged by these numbers).
"""
from __future__ import annotations

from unifideck.metadata.metacritic import (
    _parse_composer_response,
    _slug_candidates,
)


def _payload(title="Hades", critic=93, user=8.5):
    return {
        "components": [
            {"data": {"item": {
                "type": "game-title",
                "title": title,
                "criticScoreSummary": {"score": critic},
                "userScoreSummary": {"score": user},
                "description": "Defy the god of death.",
            }}},
            {"data": {"item": {"type": "other"}}},  # decoy component
        ],
    }


def test_parses_real_structure():
    r = _parse_composer_response("Hades", "hades", _payload())
    assert r is not None
    assert r.metascore == 93
    assert r.user_score == 8.5
    assert r.title == "Hades"


def test_rejects_wrong_game_page():
    # Slug landed on Hades' page but we queried Frostpunk → reject.
    assert _parse_composer_response("Frostpunk", "hades", _payload()) is None


def test_accepts_title_variant():
    # Edition/®/roman variants of the same game still validate.
    r = _parse_composer_response("Hades®", "hades", _payload(title="Hades"))
    assert r is not None and r.metascore == 93


def test_missing_item_returns_none():
    assert _parse_composer_response("X", "x", {"components": []}) is None
    assert _parse_composer_response("X", "x", {}) is None


def test_slug_candidates_deterministic_and_exact_first():
    a = _slug_candidates("Final Fantasy VII")
    b = _slug_candidates("Final Fantasy VII")
    assert a == b                      # deterministic (was a set)
    assert a[0] == "final-fantasy-vii"  # exact form first
    assert len(a) == len(set(a))        # de-duplicated
