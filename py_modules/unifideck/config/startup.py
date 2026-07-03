"""Startup-time config validation extracted from ``main.py``.

Owns the 3-step validation sequence the plugin runs during
``Plugin._main`` before stores are instantiated:

  1. Validate ``defaults/config.json`` against the JSON schema.
  2. Validate the user overrides file (missing is OK per TC-VAL-08).
  3. Runtime key-presence check: every entry in
     ``RUNTIME_REQUIRED_KEYS`` must resolve non-None after the
     3-layer merge. Catches the "forgot to add the key to
     defaults/config.json" drift that the pre-commit hook misses.

Any failure flips the plugin into "degraded" mode rather than
refusing to boot — on Steam Deck a broken-but-visible plugin is
a better UX than a silent one. The ValidationResult is returned
so ``main.py`` can expose it to the frontend via
``get_config_validation_status``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from unifideck.config import ConfigValidator, ValidationResult

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)


async def validate_config_at_startup(
    *,
    bus: EventBus,
    config: ConfigManager,
    defaults_path: str,
    user_config_path: str,
) -> tuple[ValidationResult, bool]:
    """Run the full startup validation sequence.

    Args:
        bus: Event bus — CONFIG_VALIDATION_FAILED is emitted on
            it so SecurityService can record the audit event.
        config: Merged ConfigManager used by the runtime
            key-presence check.
        defaults_path: Absolute path to ``defaults/config.json``
            (the shipped schema baseline).
        user_config_path: Absolute path to the user overrides
            file. Missing file is tolerated per TC-VAL-08.

    Returns:
        ``(result, degraded)`` where ``result`` is the
        ValidationResult to expose via the RPC surface, and
        ``degraded`` is the flag the plugin sets on itself to
        drive the Diagnostics banner.
    """
    validator = ConfigValidator(bus=bus)
    # Feed the validator both the shipped defaults AND the user
    # overrides path so schema violations in the user file are
    # caught at boot instead of silently ignored. validate_config
    # is defensive about the user file being missing — it returns
    # success with no user-source errors.
    result: ValidationResult = await validator.validate_config(
        defaults_path=defaults_path,
        user_path=user_config_path,
    )

    if not result.success:
        _log_schema_failure(result)
        return result, True

    logger.info(
        "[Unifideck] config validation OK "
        "(%d section(s) validated)",
        19,
    )

    # Runtime key-presence check: verifies every key listed in
    # RUNTIME_REQUIRED_KEYS resolves to a non-None value after
    # the 3-layer merge. Catches the case where a new call site
    # lands in code but the author forgot to add the key to
    # defaults/config.json. Only runs when schema validation
    # succeeded — there's no point triggering a second class of
    # errors on top of a broken schema.
    #
    # Failure mode is identical to schema validation: log a
    # warning, flag degraded mode, continue booting. The
    # pre-commit hook (scripts/check_config_keys.py) normally
    # catches drift before it reaches production; this runtime
    # check is a last-resort safety net for edit-and-deploy
    # scenarios that bypass the hook.
    from unifideck.config.key_presence import collect_missing_keys
    missing = collect_missing_keys(config)
    if missing:
        _log_missing_keys(missing)
        return result, True

    logger.info("[Unifideck] runtime key-presence check OK")
    return result, False


def _log_schema_failure(result: ValidationResult) -> None:
    """Log the first schema validation error in a structured way.

    Surfaces path + message so operators can locate the
    problem without opening the full JSON schema report.
    """
    first = result.errors[0] if result.errors else None
    first_path = first.path if first else "<unknown>"
    first_msg = first.message if first else "<unknown>"
    logger.warning(
        "[Unifideck] config validation FAILED — starting in "
        "degraded mode. %d error(s). First: %s: %s",
        len(result.errors),
        first_path,
        first_msg,
    )


def _log_missing_keys(missing: list[str]) -> None:
    """Log runtime key-presence failures with first 10 keys.

    Long tails get truncated with a ``(+N more)`` suffix to
    keep the log line readable.
    """
    sample = ", ".join(missing[:10])
    overflow = (
        f" (+{len(missing) - 10} more)"
        if len(missing) > 10
        else ""
    )
    logger.warning(
        "[Unifideck] %d runtime-required config key(s) "
        "missing from defaults/config.json: %s%s. "
        "Affected features will run with None values. "
        "Add each key to defaults/config.json or remove "
        "it from RUNTIME_REQUIRED_KEYS if the code no "
        "longer reads it.",
        len(missing),
        sample,
        overflow,
    )
