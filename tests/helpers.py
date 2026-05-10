"""Shared test helpers importable from any test subdirectory."""
from __future__ import annotations

from typing import Any


class MockConfig:
    """Minimal ConfigManager stub for unit tests.

    Supports dotted-key access and pre-loaded data.
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = data or {}

    def get(self, key: str, default: Any = None) -> Any:
        parts = key.split(".")
        current: Any = self._data
        for part in parts:
            if not isinstance(current, dict):
                return default
            current = current.get(part)
            if current is None:
                return default
        return current

    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        current = self._data
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value
