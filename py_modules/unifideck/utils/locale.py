"""
Shared locale utilities for Unifideck store connectors.

Provides a single source of truth for the user's language / market preference,
reading from ~/.local/share/unifideck/settings.json and falling back to the
system locale when the setting is 'auto' or absent.

Public API
----------
get_unifideck_locale() -> str
    BCP-47 language tag matching Unifideck's i18n locale files.
    Examples: 'fr-FR', 'de-DE', 'en-US', 'pt-BR', 'zh-CN'

get_unifideck_market() -> str
    ISO 3166-1 alpha-2 country code derived from the locale.
    Used by Microsoft Store APIs (market= / country= parameters).
    Examples: 'FR', 'DE', 'US', 'BR', 'CN'
"""

import json
import locale as _locale
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Path shared with main.py's get_language_preference / set_language_preference
_SETTINGS_PATH = os.path.expanduser("~/.local/share/unifideck/settings.json")

# BCP-47 tags supported by Unifideck's i18n layer (matches src/i18n/locales/).
# Any language returned by this module is guaranteed to be in this set.
SUPPORTED_LOCALES = {
    "de-DE", "en-US", "es-ES", "fr-FR", "it-IT",
    "ja-JP", "ko-KR", "nl-NL", "pl-PL", "pt-BR",
    "ru-RU", "tr-TR", "uk-UA", "zh-CN",
}

# Map POSIX 2-letter language codes → BCP-47 Unifideck locale.
# Mirrors the map in gog.py's _get_unifideck_language() so behaviour is identical.
_LANG_MAP: dict[str, str] = {
    "de": "de-DE",
    "en": "en-US",
    "es": "es-ES",
    "fr": "fr-FR",
    "it": "it-IT",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "nl": "nl-NL",
    "pl": "pl-PL",
    "pt": "pt-BR",
    "ru": "ru-RU",
    "tr": "tr-TR",
    "uk": "uk-UA",
    "zh": "zh-CN",
}

_DEFAULT_LOCALE = "en-US"
_DEFAULT_MARKET = "US"


def get_unifideck_locale() -> str:
    """
    Return the BCP-47 locale tag from Unifideck settings or the system locale.

    Priority:
      1. settings.json  ``language`` field  (explicit user choice, not 'auto')
      2. POSIX system locale (LC_ALL / LANG / etc.)
      3. Hard fallback: 'en-US'
    """
    # 1. Explicit user preference
    saved = _read_settings_language()
    if saved and saved != "auto":
        # Normalise: accept both 'fr-FR' and 'fr_FR' from the settings file
        normalised = saved.replace("_", "-")
        if normalised in SUPPORTED_LOCALES:
            logger.debug(f"[Locale] Using Unifideck setting: {normalised}")
            return normalised
        # If the saved value isn't a full BCP-47 tag, try to map its 2-letter prefix
        prefix = normalised.split("-")[0].lower()
        mapped = _LANG_MAP.get(prefix)
        if mapped:
            logger.debug(f"[Locale] Mapped setting '{saved}' → {mapped}")
            return mapped
        # Unknown locale saved — fall through to system detection
        logger.debug(f"[Locale] Unknown saved locale '{saved}', falling back to system")

    # 2. System locale
    system = _detect_system_locale()
    if system:
        logger.debug(f"[Locale] Using system locale: {system}")
        return system

    # 3. Hard fallback
    logger.debug(f"[Locale] Using default locale: {_DEFAULT_LOCALE}")
    return _DEFAULT_LOCALE


def get_unifideck_market() -> str:
    """
    Return the ISO 3166-1 alpha-2 country code from the active locale.

    Derived from the region suffix of the BCP-47 tag.
    Examples:  'fr-FR' → 'FR',  'pt-BR' → 'BR',  'zh-CN' → 'CN'
    """
    loc = get_unifideck_locale()
    parts = loc.split("-")
    if len(parts) >= 2:
        return parts[-1].upper()
    return _DEFAULT_MARKET


# ── Private helpers ─────────────────────────────────────────────────────────

def _read_settings_language() -> Optional[str]:
    """Read the raw 'language' field from settings.json, or None on any error."""
    try:
        if os.path.exists(_SETTINGS_PATH):
            with open(_SETTINGS_PATH, "r") as f:
                settings = json.load(f)
            return settings.get("language")
    except Exception as e:
        logger.debug(f"[Locale] Could not read settings.json: {e}")
    return None


def _detect_system_locale() -> Optional[str]:
    """Detect the system POSIX locale and map it to a supported BCP-47 tag."""
    try:
        lang_tuple = _locale.getlocale()
        if lang_tuple and lang_tuple[0]:
            # 'fr_FR' → 'fr'
            lang_code = lang_tuple[0].split("_")[0].lower()
            return _LANG_MAP.get(lang_code)
    except Exception as e:
        logger.debug(f"[Locale] Could not detect system locale: {e}")
    return None
