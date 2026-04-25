"""utils/locale.py — Locale detection and market resolution.

# OP-33b | py_modules/unifideck/utils/locale.py | Depends: (none)

Single source of truth for the active locale tag and market code.

The new architecture (Technical Document v1.3, Section 10) replaces
manual 14-file i18n maintenance with build-time DeepL translation:
  - ``en-US.json`` is the only file developers edit.
  - CI detects changed keys, calls DeepL API, generates 13 locale
    files, commits them to ``src/i18n/locales/``.
  - Zero runtime dependency — generated files are committed to git.
  - The frontend ``LanguageSelector`` reads available locales from
    the ``locales/`` directory.

This backend module does NOT hardcode a locale list. Instead it:
  1. Reads the user's saved preference from ``ui.language`` in config.
  2. Discovers available locale tags by scanning the ``locales/``
     directory for ``*.json`` files (the build-generated set).
  3. Falls back to system POSIX locale detection.
  4. Ultimate fallback: ``"en-US"``.

The translation pipeline itself (DeepL integration, delta detection,
CI workflow) is developed by a separate contributor and is outside
the scope of this module.
"""
from __future__ import annotations

import locale as _locale_mod
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import ConfigManager

logger = logging.getLogger(__name__)

# Config key the UI writes when the user picks a language.
_USER_LANGUAGE_KEY = "ui.language"

# Fallback when nothing else works.
_DEFAULT_LOCALE = "en-US"
_DEFAULT_MARKET = "US"


# ── Available locale discovery ───────────────────────────────


def _discover_available_locales() -> set[str]:
    """Scan ``src/i18n/locales/`` for generated *.json files.

    Returns a set of BCP-47 tags derived from filenames
    (e.g. ``fr-FR.json`` → ``"fr-FR"``). Returns empty set
    if the directory doesn't exist (e.g. in unit tests or
    before first CI build).
    """
    tags: set[str] = set()
    try:
        from ..core.paths import resolve_plugin_dir
        locales_dir = resolve_plugin_dir() / "src" / "i18n" / "locales"
        if locales_dir.is_dir():
            for f in locales_dir.iterdir():
                if f.suffix == ".json" and f.stem != "index":
                    tags.add(f.stem)
    except Exception:
        pass
    return tags


# ── Public API ───────────────────────────────────────────────


def get_unifideck_locale(
    config: ConfigManager | None = None,
) -> str:
    """Resolve the locale tag Unifideck should use this session.

    Resolution priority:
      1. User's saved ``ui.language`` if it matches an available
         locale file in ``src/i18n/locales/``. Unknown saved tags
         are silently ignored.
      2. System POSIX locale via ``_detect_system_locale``,
         2-letter prefix matched against available locales.
      3. ``"en-US"`` fallback.
    """
    available = _discover_available_locales()

    # Tier 1: explicit user preference
    if config is not None:
        try:
            user_lang = config.get(_USER_LANGUAGE_KEY)
            if user_lang and user_lang != "auto":
                if not available or user_lang in available:
                    return user_lang
        except Exception:
            pass

    # Tier 2: system POSIX locale
    sys_locale = _detect_system_locale()
    if sys_locale and available:
        prefix = sys_locale[:2].lower()
        for tag in available:
            if tag[:2].lower() == prefix:
                return tag

    return _DEFAULT_LOCALE


def get_unifideck_market(
    config: ConfigManager | None = None,
) -> str:
    """Resolve the market tag (2-letter country code).

    Extracts the country code from the BCP-47 tag (after
    the dash). For tags without a country code, looks up
    a matching available locale that does have one.
    """
    tag = get_unifideck_locale(config)

    if "-" in tag:
        return tag.split("-", 1)[1].upper()

    # No country in tag — find a matching available locale
    available = _discover_available_locales()
    if available:
        prefix = tag[:2].lower()
        for avail_tag in available:
            if avail_tag[:2].lower() == prefix and "-" in avail_tag:
                return avail_tag.split("-", 1)[1].upper()

    return _DEFAULT_MARKET


def _detect_system_locale() -> str | None:
    """Return a system locale tag from POSIX or None.

    Tries ``locale.getlocale(locale.LC_MESSAGES)``, falls
    back to ``LANG`` env var parse. Normalises the result
    to a BCP-47-ish tag (``"fr_FR.UTF-8"`` → ``"fr-FR"``).
    Returns None when nothing usable is detected.
    """
    raw = None
    try:
        raw = _locale_mod.getlocale(_locale_mod.LC_MESSAGES)[0]
    except Exception:
        pass

    if not raw:
        raw = os.environ.get("LANG", "")

    if not raw or raw in ("C", "POSIX"):
        return None

    # Normalise: "fr_FR.UTF-8" → "fr-FR"
    raw = raw.split(".")[0]
    raw = raw.replace("_", "-")

    if len(raw) < 2:
        return None

    return raw
