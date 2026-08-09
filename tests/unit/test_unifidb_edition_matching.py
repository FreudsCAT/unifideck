"""Edition-suffixed storefront titles must not match the WRONG edition.

Storefront titles carry decoration the catalog's canonical name lacks. Taking
"best match for the raw string" produced confidently wrong answers, verified
against the live catalog:

* ``DOOM Eternal Standard Edition`` → *Doom: Eternal - Collector's Edition*
* ``Hades Standard Edition``        → *Hades: Limited Edition*

A wrong record is worse than no record here — this data feeds save-location
resolution and cloud-save flags, so a neighbouring edition can hand a game
another product's save paths.

But the raw form is still right when the catalog carries that exact edition
(``The Witcher 3: Wild Hunt - Game of the Year Edition`` is its own record and
beats the base game). Hence: exact wins, then the edition-stripped base
title, then a fuzzy raw match.
"""
from __future__ import annotations

from unifideck.metadata.unifidb import pick_match, title_variants


def _catalog(*names: str) -> list[dict[str, str]]:
    return [{"name": n} for n in names]


# ── variant derivation ───────────────────────────────────────────────


def test_raw_title_is_always_tried_first() -> None:
    assert title_variants("Hades Standard Edition")[0] == "Hades Standard Edition"


def test_edition_suffix_produces_a_second_variant() -> None:
    assert title_variants("Hades Standard Edition")[1] == "hades"


def test_plain_title_still_yields_a_normalised_variant() -> None:
    """Normalisation alone can differ from the raw string; that is fine —
    both forms are tried and the exact-match rule picks correctly."""
    assert title_variants("Bastion")[0] == "Bastion"


def test_no_duplicate_variants() -> None:
    variants = title_variants("bastion")
    assert len(variants) == len(set(variants))


# ── selection ────────────────────────────────────────────────────────


def test_base_game_beats_a_different_edition() -> None:
    """The DOOM/Hades case: never settle for a neighbouring edition."""
    games = _catalog("Hades: Limited Edition", "Hades")
    picked = pick_match(title_variants("Hades Standard Edition"), games)
    assert picked is not None
    assert picked["name"] == "Hades"


def test_exact_edition_record_wins_when_the_catalog_has_it() -> None:
    games = _catalog(
        "The Witcher 3: Wild Hunt",
        "The Witcher 3: Wild Hunt - Game of the Year Edition",
    )
    picked = pick_match(
        title_variants("The Witcher 3: Wild Hunt - Game of the Year Edition"),
        games,
    )
    assert picked is not None
    assert picked["name"].endswith("Game of the Year Edition")


def test_plain_title_matches_its_own_record() -> None:
    picked = pick_match(title_variants("Bastion"), _catalog("Bastion"))
    assert picked is not None
    assert picked["name"] == "Bastion"


def test_no_match_returns_none() -> None:
    assert pick_match(title_variants("Bastion"), _catalog("Celeste")) is None


def test_empty_catalog_returns_none() -> None:
    assert pick_match(title_variants("Bastion"), []) is None


def test_sequel_is_still_rejected() -> None:
    """The 0.85 threshold guarding against franchise confusion must hold —
    loosening matching to chase misses would trade "no data" for "wrong
    data", which is the worse failure for save paths."""
    assert pick_match(title_variants("Spelunky"), _catalog("Spelunky 2")) is None
