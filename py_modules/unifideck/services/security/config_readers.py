"""services.security.config_readers — Defensive ConfigManager readers.

Four free functions that read typed values out of a
``ConfigManager`` with a graceful fallback when the config is
absent, malformed, or the caller is running in a test harness
that passed ``None``.

Extracted from ``security_service.py`` on 2026-04-18. The four
functions were originally methods of ``SecurityService`` but
they never touched ``self`` beyond reading ``self._config``, so
making them module-level with an explicit ``config`` parameter
reads more cleanly and is trivially testable.

Why ``config`` can be ``None``
------------------------------
SecurityService accepts ``config=None`` for unit tests and for
the subset-bootstrap path used by the standalone launcher CLI.
All four functions treat None as "fall through to default".
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unifideck.config import ConfigManager


def read_int(
    config: ConfigManager | None, key: str, default: int,
) -> int:
    """Read an int config key with fallback.

    Returns ``default`` if:
      - config is None
      - config has no ``get`` method
      - the value is empty/falsy
      - the value can't be cast to int
    """
    if config is None or not hasattr(config, "get"):
        return default
    try:
        val = config.get(key, default)
        return int(val) if val else default
    except (TypeError, ValueError):
        return default


def read_float(
    config: ConfigManager | None, key: str, default: float,
) -> float:
    """Read a float config key with fallback.

    Same semantics as ``read_int`` but for float values.
    """
    if config is None or not hasattr(config, "get"):
        return default
    try:
        val = config.get(key, default)
        return float(val) if val else default
    except (TypeError, ValueError):
        return default


def read_str(
    config: ConfigManager | None, key: str, default: str,
) -> str:
    """Read a string config key with fallback.

    Returns ``default`` if config is None or the value is empty.
    Never raises: stringifies whatever the config returns.
    """
    if config is None or not hasattr(config, "get"):
        return default
    val = config.get(key, default)
    return str(val) if val else default


def read_list(
    config: ConfigManager | None, key: str,
) -> list[str]:
    """Read a list[str] config key, returns [] on absence.

    Filters out non-string and empty-string entries — the caller
    (``_handle_device_reset``) expects absolute-ish file paths.
    """
    if config is None or not hasattr(config, "get"):
        return []
    val = config.get(key, None)
    if not isinstance(val, list):
        return []
    return [str(x) for x in val if isinstance(x, str) and x]
