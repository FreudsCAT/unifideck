"""Artwork source fixes: cleaned-query matching + Microsoft displaycatalog art.

Two regressions surfaced after the backfill fix landed:

* ``Besiege + The Splintered Sea DLC`` resolved to no SGDB id — the
  autocomplete returned "Besiege" but the scorer compared it against the
  raw, noisy title (Jaccard 0.2). ``_query_forms`` now also matches the
  cleaned query.
* Microsoft games (often with mangled xCloud titles like "HALO5") had no
  store art. ``displaycatalog`` — already fetched for titles — carries
  authoritative box art keyed on the productId.
"""
from __future__ import annotations

from unifideck.services.artwork.store_metadata import _extract_ms_images
from unifideck.steam.steamgriddb.search import _query_forms


def test_query_forms_includes_cleaned_query():
    # The raw title is noisy ("+ ... DLC"); the cleaned form "besiege"
    # must be a match target so an exact "Besiege" candidate is accepted.
    forms = _query_forms("Besiege + The Splintered Sea DLC")
    norms = {n for n, _ in forms}
    assert "besiege" in norms
    # full form preserved too
    assert any("splintered" in n for n in norms)


def test_query_forms_dedups_when_clean_is_noop():
    forms = _query_forms("Control")
    assert forms == [("control", "control")]


def test_ms_images_map_purposes_to_kinds():
    images = [
        {"ImagePurpose": "Poster", "Uri": "//cdn/poster"},
        {"ImagePurpose": "SuperHeroArt", "Uri": "//cdn/superhero"},
        {"ImagePurpose": "TitledHeroArt", "Uri": "//cdn/titled"},
        {"ImagePurpose": "Logo", "Uri": "//cdn/logo"},
        {"ImagePurpose": "BoxArt", "Uri": "//cdn/box"},
    ]
    out = _extract_ms_images(images)
    assert out["grid"] == "https://cdn/poster"          # Poster → portrait grid
    assert out["hero"] == "https://cdn/superhero"        # SuperHeroArt → hero
    assert out["grid_l"] == "https://cdn/titled"         # TitledHeroArt → landscape
    # Logo/icon intentionally left to SGDB (MS Logo is a square tile).
    assert "logo" not in out and "icon" not in out


def test_ms_images_priority_fallbacks():
    # No Poster → BrandedKeyArt for grid; no SuperHeroArt → Hero for hero.
    images = [
        {"ImagePurpose": "BrandedKeyArt", "Uri": "//cdn/branded"},
        {"ImagePurpose": "Hero", "Uri": "//cdn/hero"},
    ]
    out = _extract_ms_images(images)
    assert out["grid"] == "https://cdn/branded"
    assert out["hero"] == "https://cdn/hero"
    assert out["grid_l"] == "https://cdn/hero"  # Hero is grid_l's fallback


def test_ms_images_empty_when_no_usable_purposes():
    assert _extract_ms_images([{"ImagePurpose": "Screenshot", "Uri": "//c/s"}]) == {}
    assert _extract_ms_images([]) == {}


def test_match_shim_reexports_shared_util():
    # The SGDB ``match`` module is now a shim over the shared util; pin
    # that it forwards the exact same objects so the two can't drift.
    from unifideck.steam.steamgriddb import match as shim
    from unifideck.utils import title_match as canon

    for name in (
        "normalize_for_match", "strip_edition_suffix",
        "score_match", "clean_search_query", "EDITION_SUFFIXES",
    ):
        assert getattr(shim, name) is getattr(canon, name)
