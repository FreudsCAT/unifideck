"""services/launch_history/config_readers.py — Live config accessors.

Pure helpers reading circuit-breaker tuning knobs from a
``ConfigManager`` on every call. No caching — users can change
settings via the UI between launches and new values must apply
immediately. All three accept ``config=None`` so the service
can delegate trivially during tests or launcher-subprocess use.
"""
from __future__ import annotations

from typing import Any

# Defaults — also mirrored on LaunchHistoryService as class attrs
# for backwards compat. Source of truth lives here.
DEFAULT_THRESHOLD = 3
DEFAULT_WINDOW_SECONDS = 600.0  # 10 minutes
DEFAULT_FAST_BOOT_SECONDS = 10.0


def read_threshold(config: Any | None) -> int:
    """Return ``circuit_breaker.failures_threshold`` (default 3)."""
    if config is None:
        return DEFAULT_THRESHOLD
    return config.get("circuit_breaker.failures_threshold", DEFAULT_THRESHOLD)


def read_window_seconds(config: Any | None) -> float:
    """Return ``circuit_breaker.window_seconds`` (default 600.0)."""
    if config is None:
        return DEFAULT_WINDOW_SECONDS
    return config.get("circuit_breaker.window_seconds", DEFAULT_WINDOW_SECONDS)


def read_fast_boot_seconds(config: Any | None) -> float:
    """Return ``circuit_breaker.fast_boot_seconds`` (default 10.0)."""
    if config is None:
        return DEFAULT_FAST_BOOT_SECONDS
    return config.get("circuit_breaker.fast_boot_seconds", DEFAULT_FAST_BOOT_SECONDS)
