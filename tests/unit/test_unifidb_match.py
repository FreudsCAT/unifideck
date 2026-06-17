"""UnifiDB title-fallback now gated by the shared ``titles_match``.

UnifiDB resolves by store-native id first (exact); only when a bucket has
no matching id does it fall back to title matching. That fallback used a
local substring scorer that accepted sequels at 0.85 (≥ the 0.65
threshold) — "Hades" → "Hades II", "Quake" → "Quake II" — feeding the
wrong game's description/genres on UnifiDB-only fields. It now uses
``titles_match`` (reject sequels, accept publisher/roman/edition variants).
"""
from __future__ import annotations

from unifideck.metadata.unifidb import get_best_match


def _c(*names):
    return [{"title": n} for n in names]


def test_rejects_sequel_only_bucket():
    # Bucket has only the sequel — must return None, not a wrong 0.85 match.
    assert get_best_match("Hades", _c("Hades II")) is None
    assert get_best_match("Spelunky", _c("Spelunky 2")) is None


def test_picks_correct_over_sequel():
    assert get_best_match("Hades", _c("Hades II", "Hades"))["title"] == "Hades"
    assert get_best_match("Quake", _c("Quake II", "Quake"))["title"] == "Quake"


def test_accepts_roman_numeral_variant():
    r = get_best_match("Assassin's Creed II", _c("Assassin's Creed 2"))
    assert r is not None and r["title"] == "Assassin's Creed 2"


def test_ranks_exact_over_edition():
    r = get_best_match("Control", _c("CONTROL Ultimate Edition", "Control"))
    assert r["title"] == "Control"


def test_empty_candidates():
    assert get_best_match("Anything", []) is None
