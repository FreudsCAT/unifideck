"""Launch history config readers — typed parsers.

OP-21d | py_modules/unifideck/services/launch_history/config_readers.py

Three readers for the launch-history tunables :

* ``read_threshold`` — failure count that opens the circuit;
* ``read_window_seconds`` — rolling window over which failures
  accumulate;
* ``read_fast_boot_seconds`` — duration under which an exit is
  treated as a "quick exit" (likely a crash).
"""

from __future__ import annotations
from typing import Any

DEFAULT_THRESHOLD = 3
DEFAULT_WINDOW_SECONDS = 600.0
DEFAULT_FAST_BOOT_SECONDS = 10.0


def read_threshold(config: Any | None) -> int:
    """Read threshold."""
    if config is None:
        return DEFAULT_THRESHOLD
    return int(
        config.get_int(
            "circuit_breaker.failures_threshold",
            DEFAULT_THRESHOLD,
        )
    )


def read_window_seconds(config: Any | None) -> float:
    """Read window seconds."""
    if config is None:
        return DEFAULT_WINDOW_SECONDS
    return float(
        config.get_int(
            "circuit_breaker.window_seconds",
            int(DEFAULT_WINDOW_SECONDS),
        )
    )


def read_fast_boot_seconds(config: Any | None) -> float:
    """Read fast boot seconds."""
    if config is None:
        return DEFAULT_FAST_BOOT_SECONDS
    return float(
        config.get_int(
            "circuit_breaker.fast_boot_seconds",
            int(DEFAULT_FAST_BOOT_SECONDS),
        )
    )
