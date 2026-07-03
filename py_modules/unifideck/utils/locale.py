"""utils/locale.py — Locale detection and market resolution.

Single source of truth: the `i18n.locales` section of
defaults/config.json (parsed and validated by
scripts/locale_config.py). This module never hardcodes a list
of supported locales — adding a new language is a one-line
edit to config.json, and this module picks it up automatically
on the next plugin start.

Resolution priority:
  1. User preference → ConfigManager key 'ui.language'
     (only if the saved value is a tag present in
     i18n.locales; unknown saved tags are silently ignored
     and we fall through to system detection)
  2. System POSIX → locale.getlocale() → mapped to the first
     i18n.locales entry whose tag begins with the same 2-
     letter prefix (case-insensitive)
  3. Source fallback → LocaleConfig.source.tag
"""
from __future__ import annotations

import locale as _locale
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config_helpers import get_cfg

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)

# Config keys used by this module.
_USER_LANGUAGE_KEY = "ui.language"

# The locale_config module lives in scripts/ which is a sibling
# of py_modules/. It's not on the default Python path so we add
# it on first use. This import is done lazily to avoid paying
# the cost on every module import.
_LOCALE_CONFIG_MODULE = None


def _import_locale_config() -> Any | None:
    """Lazy import of scripts/locale_config.py, cached.

    Returns the module handle or None if scripts/ can't be
    located (unusual install layout). None signals "no schema
    validation available" and the caller falls through to its
    default behaviour.
    """
    global _LOCALE_CONFIG_MODULE
    if _LOCALE_CONFIG_MODULE is not None:
        return _LOCALE_CONFIG_MODULE  # type: ignore[unreachable]  # fallback for missing import
    # Walk up from this file to find <repo>/scripts/locale_config.py.
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent.parent / "scripts",
        # Fallback: some dev layouts may differ
        here.parent.parent.parent / "scripts",
    ]
    for scripts_dir in candidates:
        target = scripts_dir / "locale_config.py"
        if target.is_file():
            scripts_str = str(scripts_dir)
            if scripts_str not in sys.path:
                sys.path.insert(0, scripts_str)
            try:
                # Dynamic import from ``scripts/`` (injected on
                # sys.path above). Mypy can't see the module
                # statically; the ImportError below catches any
                # failure including the missing-file case.
                import locale_config  # type: ignore[import-not-found]
                _LOCALE_CONFIG_MODULE = locale_config
                return _LOCALE_CONFIG_MODULE
            except ImportError as e:
                logger.debug(
                    "[locale] Found %s but import failed: %s",
                    target, e,
                )
                return None
    logger.debug(
        "[locale] scripts/locale_config.py not found on any "
        "candidate path; locale resolution will use degraded "
        "mode",
    )
    return None


def get_locale_config(config: ConfigManager | None) -> Any | None:
    """Return the parsed i18n.locales section, or None on failure.

    The returned object (a LocaleConfig from
    scripts/locale_config.py) exposes `all_tags`, `get(tag)`,
    `source`, and `targets` for consumers that need the full
    list.

    Returns None if config has no i18n section or the scripts
    helper is unavailable.
    """
    lc_module = _import_locale_config()
    if lc_module is None:
        return None
    # ConfigManager exposes the full merged dict via _merged,
    # but accessing a private attribute would be fragile.
    # Instead we rebuild the i18n branch using public .get().
    i18n_section = get_cfg(config, "i18n", None)
    if not isinstance(i18n_section, dict):
        return None
    try:
        return lc_module.load_from_dict(
            {"i18n": i18n_section},
        )
    except Exception as e:
        # Validation failed — log and return None. Runtime
        # code must never crash just because someone edited
        # config.json incorrectly.
        logger.warning(
            "[locale] i18n schema validation failed at "
            "runtime: %s", e,
        )
        return None


def get_unifideck_locale(config: ConfigManager | None) -> str:
    """Return the BCP-47 locale tag to use for UI and store APIs.

    Returns a BCP-47 tag that is guaranteed to exist in
    i18n.locales, or a degraded fallback if the config is
    unavailable. The fallback is 'en-US' only as a last
    resort — normal operation always returns a config-sourced
    tag.
    """
    lc = get_locale_config(config)
    # ─── 1. Explicit user preference ──────────────────────────
    saved = get_cfg(config, _USER_LANGUAGE_KEY, None)
    if isinstance(saved, str) and saved:
        # Normalise: accept both 'fr-FR' and 'fr_FR' for
        # compatibility with POSIX-style values.
        normalised = saved.replace("_", "-")
        if lc is not None and lc.get(normalised) is not None:
            logger.debug(
                "[locale] user preference: %s", normalised,
            )
            return normalised
        if lc is None:
            # No canonical list available — trust the saved
            # value as-is. This is the degraded path.
            logger.debug(
                "[locale] user preference (unvalidated): %s",
                normalised,
            )
            return normalised
        logger.debug(
            "[locale] user preference '%s' not in "
            "i18n.locales, falling back to system", saved,
        )
    # ─── 2. System POSIX locale ───────────────────────────────
    system = _detect_system_locale(lc)
    if system:
        logger.debug("[locale] system: %s", system)
        return system
    # ─── 3. Source fallback ───────────────────────────────────
    if lc is not None:
        source_tag = str(lc.source.tag)
        logger.debug(
            "[locale] source fallback: %s", source_tag,
        )
        return source_tag
    # Final degraded fallback: hardcoded en-US only when the
    # config system is completely broken.
    logger.warning(
        "[locale] no config available, using hardcoded "
        "en-US",
    )
    return "en-US"


def get_unifideck_market(config: ConfigManager | None) -> str:
    """Return the ISO 3166-1 alpha-2 market code.

    Derived from the region suffix of the active locale tag.
    Examples: 'fr-FR' → 'FR', 'pt-BR' → 'BR', 'zh-CN' → 'CN'.
    Falls back to 'US' only when the active locale has no
    region suffix.
    """
    tag = get_unifideck_locale(config)
    parts = tag.split("-")
    if len(parts) >= 2 and len(parts[-1]) == 2:
        return parts[-1].upper()
    return "US"


# ══════════════════════════════════════════════════════════════════
# Private helpers
# ══════════════════════════════════════════════════════════════════


def _detect_system_locale(lc: Any) -> str | None:
    """Map the POSIX system locale to an i18n.locales tag.

    Returns a tag from lc.all_tags whose 2-letter prefix
    matches the POSIX lang code, or None on any failure.
    """
    if lc is None:
        return None
    try:
        lang_tuple = _locale.getlocale()
    except (ValueError, TypeError) as e:
        logger.debug("[locale] getlocale() failed: %s", e)
        return None
    if not lang_tuple or not lang_tuple[0]:
        return None
    # lang_tuple[0] is like 'fr_FR' or 'en_US' — extract the
    # 2-letter prefix in a case-insensitive way.
    prefix = lang_tuple[0].split("_")[0].lower()
    if not prefix:
        return None
    # Find the first entry in the canonical list whose tag
    # starts with this prefix. Order matches config.json,
    # which is also the UI dropdown order.
    for loc in lc.locales:
        if (
            loc.tag.lower().startswith(prefix + "-")
            or loc.tag.lower() == prefix
        ):
            return str(loc.tag)
    return None
