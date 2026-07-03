"""Tests for GOG install language normalization + matching.

Covers the inconsistent label formats gogdl emits per title and the
three-tier ``smart_match_language`` (exact → 2-letter prefix →
normalized base). Mirrors the frontend
``src/lib/i18n/gog-language-match.test.ts``.
"""

from __future__ import annotations

import pytest

from unifideck.stores.gog.install.helpers import _InstallHelpers
from unifideck.stores.gog.install.languages import (
    normalize_language,
    smart_match_language,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("English", "en"),
        ("Spanish", "es"),
        ("Deutsch", "de"),
        ("Français", "fr"),
        ("en", "en"),
        ("en-US", "en"),
        ("pt-BR", "pt"),
        ("fr_FR", "fr"),
        ("eng", "en"),
        ("esp", "es"),  # GOG legacy Spanish
        ("spa", "es"),
        ("br", "pt"),  # GOG legacy Brazilian
        ("cn", "zh"),
        ("English (en)", "en"),
        ("Spanish (esp)", "es"),
        ("en (English)", "en"),
        ("Klingon", None),
        ("", None),
    ],
)
def test_normalize_language(raw: str, expected: str | None) -> None:
    assert normalize_language(raw) == expected


def test_smart_match_exact() -> None:
    assert smart_match_language("es-ES", ["en-US", "es-ES"]) == "es-ES"


def test_smart_match_prefix() -> None:
    assert smart_match_language("es-ES", ["en", "es"]) == "es"


def test_smart_match_normalized_across_formats() -> None:
    # User picked "es-ES" but the build lists full English names.
    assert smart_match_language("es-ES", ["English", "Spanish"]) == "Spanish"
    # GOG legacy code on the supported side.
    assert smart_match_language("Spanish", ["en", "esp"]) == "esp"


def test_smart_match_no_match_returns_none() -> None:
    assert smart_match_language("ja-JP", ["English", "French"]) is None
    assert smart_match_language("", ["en"]) is None
    assert smart_match_language("en", []) is None


def test_pick_languages_explicit_is_verbatim() -> None:
    """An explicit (user-picked) language is passed to gogdl VERBATIM —
    never remapped. ``es-MX`` must stay ``es-MX`` even if the probed
    ``supported`` list only has ``es-ES``/English (the bug where the
    pick was dropped to a different variant or English)."""
    assert _InstallHelpers.pick_languages(
        "es-MX", True, ["en-US", "es-ES"],
    ) == ["es-MX"]
    # Even with an empty/failed probe, the pick is honored verbatim.
    assert _InstallHelpers.pick_languages("es-MX", True, []) == ["es-MX"]


def test_pick_languages_implicit_still_matches() -> None:
    """The implicit (no user pick) path is a best-effort default and
    may still match the system locale against the game's languages."""
    assert _InstallHelpers.pick_languages(
        "es-ES", False, ["en-US", "es-ES"],
    ) == ["es-ES"]
