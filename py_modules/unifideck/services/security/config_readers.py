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
    """Read int."""
    if config is None or not hasattr(config, "get"):
        return default
    try:
        val = config.get(key, default)
        return int(val) if val else default
    except (TypeError, ValueError):
        return default


def read_float(config: ConfigManager | None, key: str, default: float) -> float:
    """Read float."""
    if config is None or not hasattr(config, "get"):
        return default
    try:
        val = config.get(key, default)
        return float(val) if val else default
    except (TypeError, ValueError):
        return default


def read_str(config: ConfigManager | None, key: str, default: str) -> str:
    """Read str."""
    if config is None or not hasattr(config, "get"):
        return default
    val = config.get(key, default)
    return str(val) if val else default


def read_list(config: ConfigManager | None, key: str) -> list[str]:
    """Read list."""
    if config is None or not hasattr(config, "get"):
        return []
    val = config.get(key, None)
    if not isinstance(val, list):
        return []
    return [str(x) for x in val if isinstance(x, str) and x]
