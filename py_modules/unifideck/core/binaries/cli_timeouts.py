"""core/binaries/cli_timeouts.py — Shared CLI timeout configuration.

Moved from core/ to core/binaries/ (colocated with binary_resolver and binary_signatures). Clean break: no shim.

All stores that wrap a CLI tool (legendary for Epic, nile for
Amazon, gogdl for GOG) need per-operation timeouts:
 - auth_check: quick existence probe on the auth file/command
 - version_check: `--version` probe to verify the CLI runs
 - library_fetch: `list`/`list-installed` calls
 - install_poll: polling for install progress
 - uninstall: wait for uninstall command to return
Rather than duplicate the defaults and the config lookup in every
store, this module exposes a single `read_cli_timeouts(config)`
function that reads the `cli_timeouts.*` block from ConfigManager
and falls back to sensible defaults.
The returned dict is intended to be captured once in each store's
constructor so hot paths don't re-parse config on every call.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unifideck.config import ConfigManager


# Hardcoded defaults matching the legacy code's behaviour. Stores
# can override individual values via config.cli_timeouts.*
DEFAULT_TIMEOUTS: dict[str, int] = {
 "auth_check": 10,
 "version_check": 2,
 "library_fetch": 30,
 "install_poll": 60,
 "uninstall": 120,
}
def read_cli_timeouts(config: ConfigManager | None) -> dict[str, int]:
    """Return a timeout dict populated from config with defaults.

    Args:
    config: ConfigManager instance with dot-notation .get(),
    or None (returns the defaults unchanged).

    Returns:
    Dict with keys {auth_check, version_check, library_fetch,
    install_poll, uninstall}, all int-valued. Missing keys in
    config fall back to DEFAULT_TIMEOUTS. Values that can't
    be coerced to int also fall back silently.

    """
    if config is None:
        return dict(DEFAULT_TIMEOUTS)
    out: dict[str, int] = {}
    for key, default in DEFAULT_TIMEOUTS.items():
        try:
            val = int(config.get(f"cli_timeouts.{key}", default))
            out[key] = val if val > 0 else default
        except (TypeError, ValueError):
            out[key] = default
    return out
