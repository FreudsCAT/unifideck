"""core/bin/cli_timeouts.py — Shared CLI timeout configuration.

# OP-07c | core/bin/cli_timeouts.py | Depends: OP-11a

Single source of truth for per-operation timeouts used by CLI-
wrapping stores (legendary/Epic, nile/Amazon, gogdl/GOG).
Stores capture the dict once in their constructor so hot paths
don't re-parse config on every call.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...config import ConfigManager

# Hardcoded defaults matching the legacy behaviour. Stores can
# override individual values via config.cli_timeouts.*
DEFAULT_TIMEOUTS: dict[str, int] = {
    "auth_check": 10,
    "version_check": 2,
    "library_fetch": 30,
    "install_poll": 60,
    "uninstall": 120,
}


def read_cli_timeouts(config: ConfigManager | None) -> dict[str, int]:
    """Return timeouts dict populated from ``config`` with defaults.
    Missing or non-int values fall back to ``DEFAULT_TIMEOUTS``
    silently. ``None`` config returns a copy of defaults unchanged.
    """
    raise NotImplementedError("OP-07c: merge config.cli_timeouts.* over DEFAULT_TIMEOUTS")
