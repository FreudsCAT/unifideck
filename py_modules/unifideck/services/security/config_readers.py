"""Security config readers — typed parsers from the user config.

OP-19d | py_modules/unifideck/services/security/config_readers.py

Three pure functions to read security tunables from the user config
with strict typing :

* ``read_int(config, key, default)``   — integer with fallback;
* ``read_float(config, key, default)`` — float with fallback;
* ``read_str(config, key, default)``   — string with fallback.

Used by the security service constructor to read its tunables in
one place.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...config import ConfigManager


def read_int(config: ConfigManager | None, key: str, default: int) -> int:
    """Read an integer from the config with a fallback default.

    Three failure modes all return ``default``:

    * config is ``None`` or doesn't expose ``get``;
    * the key is absent or the value is falsy (None/0/"");
    * the value can't be coerced to ``int``.

    Args:
        config: optional config manager.
        key: dotted config path.
        default: value returned on any failure.

    Returns:
        Parsed integer, or ``default``.
    """
    if config is None or not hasattr(config, "get"):
        return default
    try:
        val = config.get(key, default)
        return int(val) if val else default
    except (TypeError, ValueError):
        return default


def read_float(config: ConfigManager | None, key: str, default: float) -> float:
    """Read a float from the config with a fallback default.

    Same failure semantics as ``read_int``.

    Args:
        config: optional config manager.
        key: dotted config path.
        default: value returned on any failure.

    Returns:
        Parsed float, or ``default``.
    """
    if config is None or not hasattr(config, "get"):
        return default
    try:
        val = config.get(key, default)
        return float(val) if val else default
    except (TypeError, ValueError):
        return default


def read_str(config: ConfigManager | None, key: str, default: str) -> str:
    """Read a string from the config with a fallback default.

    Coerces non-string truthy values via ``str()``. Empty
    strings + ``None`` fall back to ``default``.

    Args:
        config: optional config manager.
        key: dotted config path.
        default: value returned on absence or empty string.

    Returns:
        String value or ``default``.
    """
    if config is None or not hasattr(config, "get"):
        return default
    val = config.get(key, default)
    return str(val) if val else default


def read_list(config: ConfigManager | None, key: str) -> list[str]:
    """Read a list-of-strings from the config.

    Defensive parsing — anything that isn't actually a list, or
    contains non-string / empty entries, is filtered out. Returns
    an empty list rather than ``None`` on absence, so callers can
    iterate unconditionally.

    Args:
        config: optional config manager.
        key: dotted config path.

    Returns:
        List of non-empty strings; empty if absent or malformed.
    """
    if config is None or not hasattr(config, "get"):
        return []
    val = config.get(key, None)
    if not isinstance(val, list):
        return []
    return [str(x) for x in val if isinstance(x, str) and x]
