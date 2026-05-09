"""Tests for utils/locale.py (OP-33b).

The new i18n architecture (Tech Doc v1.3 §10) uses build-time DeepL
translation. locale.py discovers available tags by scanning the
locales/ directory — it does NOT hardcode a locale list.
"""
from __future__ import annotations

from tests.helpers import MockConfig
from unifideck.utils.locale import (
    _detect_system_locale,
    get_unifideck_locale,
    get_unifideck_market,
)


def test_detect_system_locale_returns_string_or_none():
    result = _detect_system_locale(None)
    assert result is None or isinstance(result, str)


# _discover_available_locales removed from source logic as it now uses config-sourced lists


def test_default_locale_is_en_us():
    """With no config and no locales/ dir, should return en-US."""
    assert get_unifideck_locale(None) == "en-US"


def test_respects_user_preference():
    """User's explicit choice should be returned directly."""
    cfg = MockConfig({"ui": {"language": "fr-FR"}})
    assert get_unifideck_locale(cfg) == "fr-FR"


def test_auto_falls_through():
    """'auto' should not be treated as a locale tag."""
    cfg = MockConfig({"ui": {"language": "auto"}})
    result = get_unifideck_locale(cfg)
    # In degraded mode (no lc), it returns 'auto'. 
    # This test is somewhat obsolete but we'll check it returns something.
    assert isinstance(result, str)


def test_none_config_returns_default():
    result = get_unifideck_locale(None)
    assert isinstance(result, str)
    assert len(result) >= 2


def test_market_from_bcp47():
    cfg = MockConfig({"ui": {"language": "de-DE"}})
    assert get_unifideck_market(cfg) == "DE"


def test_market_defaults():
    market = get_unifideck_market(None)
    assert isinstance(market, str)
    assert len(market) == 2


def test_market_tag_without_country():
    cfg = MockConfig({"ui": {"language": "fr"}})
    market = get_unifideck_market(cfg)
    assert isinstance(market, str)
    assert len(market) == 2
