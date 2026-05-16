"""config/i18n_schema.py — i18n schema validation for ConfigManager.

Moved from core/config_schema.py to unifideck.config and
renamed to i18n_schema to dispel the name clash with config/schema.json
(the JSON Schema Draft-07 descriptor used by ConfigValidator).

This module specifically validates the i18n section of the merged
configuration against the locale_config catalogue shipped under
scripts/. It is intentionally independent from the broader JSON
Schema validator in config/validator.py: the two validate different
things (locale identifiers vs. shape of the whole config.json).

Responsibilities:

  1. Locating the shared scripts/locale_config.py module
  2. Injecting scripts/ into sys.path for the import
  3. Calling load_from_dict and converting its errors to logs

A shim at core/config_schema.py preserves backward compatibility
for any caller still importing from the old path.

Why delegate to scripts/locale_config.py rather than duplicating
the schema here: the build-time translator at
scripts/translate_at_build.py already uses locale_config to decide
which locales to generate. A duplicate schema definition would
drift and produce cryptic runtime errors the next time someone
adds a locale without touching both copies.

Reference: Technical Document v1.0 — Section 3.4.10 (i18n pipeline).
"""
from __future__ import annotations

import contextlib
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ConfigSchemaError(Exception):
    """Raised when the merged config violates the declared schema.

    Distinct from ValueError/KeyError so callers can catch schema
    violations specifically and report them as startup failures
    rather than random runtime glitches.
    """


def validate_i18n_schema(
    merged: dict[str, Any],
    defaults_path: Path | None,
) -> None:
    """Validate the `i18n` section of the merged config.

    Behaviour:
      - No i18n section → return silently (legacy configs pre-date
        the i18n feature and must stay loadable)
      - scripts/locale_config.py missing → log at DEBUG and return
        (development checkouts may not ship the scripts/ folder)
      - LocaleConfigError from locale_config → log at ERROR and
        re-raise as ConfigSchemaError so the plugin fails loudly
        at startup rather than silently at runtime

    Args:
      merged: the fully-merged config dict (defaults + user overrides)
      defaults_path: path to defaults/config.json, used to locate
        scripts/ relative to it (defaults_path.parent.parent/scripts)

    Raises:
      ConfigSchemaError: if i18n is present and fails validation

    """
    if "i18n" not in merged:
        return # legacy config — tolerable

    if defaults_path is None:
        logger.debug(
            "[config_schema] defaults_path=None — cannot locate "
            "scripts/ for i18n validation, skipping",
        )
        return

    scripts_dir = defaults_path.parent.parent / "scripts"
    if not (scripts_dir / "locale_config.py").is_file():
        logger.debug(
            "[config_schema] locale_config.py not found at %s — "
            "skipping i18n schema validation",
            scripts_dir,
        )
        return

    # Temporarily inject scripts/ into sys.path so we can import
    # locale_config. Use a try/finally to always restore the
    # original path even if the import or validation raises.
    # `added` tracks whether WE are the ones who inserted — we only
    # remove on our way out, never removing a path an earlier caller
    # (or the user's PYTHONPATH) already put there.
    scripts_str = str(scripts_dir)
    added = False
    if scripts_str not in sys.path:
        sys.path.insert(0, scripts_str)
        added = True
    try:
        # ``locale_config`` lives in ``scripts/`` next to the
        # package, dynamically injected on sys.path above. Mypy
        # can't resolve it statically; the import is guarded by
        # the ``is_file()`` check earlier so a missing file
        # degrades gracefully.
        from locale_config import (  # type: ignore[import-not-found]
            LocaleConfigError,
            load_from_dict,
        )
        try:
            load_from_dict(merged)
        except LocaleConfigError as e:
            logger.exception("[config_schema] i18n schema validation failed")
            raise ConfigSchemaError(
                f"i18n schema violation: {e}",
            ) from e
    finally:
        if added:
            with contextlib.suppress(ValueError):
                sys.path.remove(scripts_str)


# ── Legacy compatibility alias ─────────────────────────────────
# The original name was ``validate_i18n``. Some callers import
# that form; keep it as an alias so the rename stays non-
# breaking until every call site is updated.
validate_i18n = validate_i18n_schema
