"""config/config_manager.py — Centralized configuration service.

# OP-11a | config/config_manager.py | Depends: OP-05
"""
from __future__ import annotations
from typing import Any


class ConfigManager:
    """Single source of truth for all plugin configuration.

    Merges defaults/config.json with user overrides. Provides
    typed access with hot-reload capability.
    """

    def __init__(self, defaults_path: str, user_path: str | None = None) -> None:
        raise NotImplementedError("OP-11a: load + merge defaults and user config")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by dot-notation key."""
        raise NotImplementedError("OP-11a: traverse nested dict by dotted key")

    def set(self, key: str, value: Any) -> None:
        """Set a config value and persist to user config."""
        raise NotImplementedError("OP-11a: update nested dict, persist")

    async def reload(self) -> bool:
        """Reload user config from disk. Return True on success."""
        raise NotImplementedError("OP-11a: re-read user config file")

    def validate(self) -> list[str]:
        """Return list of validation errors, empty if valid."""
        raise NotImplementedError("OP-11a: delegate to validator.py")

    @property
    def cli_timeouts(self) -> dict[str, int]:
        """Return CLI timeout dict for store constructors."""
        raise NotImplementedError("OP-11a: return self.get('cli_timeouts', {})")
