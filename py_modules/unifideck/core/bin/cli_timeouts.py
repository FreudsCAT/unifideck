"""Per-CLI subprocess timeout reader.

OP-08d2 | py_modules/unifideck/core/bin/cli_timeouts.py

External CLI tools (Legendary, Nile, …) sometimes hang
indefinitely on flaky network or broken accounts. Every store
wraps its CLI invocations in ``asyncio.wait_for`` with a
budget derived from this module.

``DEFAULT_TIMEOUTS`` is the baked-in table — sensible
defaults for the five known categories. ``read_cli_timeouts``
merges in any overrides from the config (``cli_timeouts.<key>``)
with defensive coercion to ``int``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...config import ConfigManager

DEFAULT_TIMEOUTS: dict[str, int] = {
    "auth_check": 10,
    "version_check": 2,
    "library_fetch": 30,
    "install_poll": 60,
    "uninstall": 120,
}


def read_cli_timeouts(config: ConfigManager | None) -> dict[str, int]:
    """Return the per-category CLI subprocess timeouts in seconds.

    Merges ``DEFAULT_TIMEOUTS`` with any config overrides at
    ``cli_timeouts.<key>``. The defaults are always returned
    in full — missing or invalid overrides fall back to the
    bundled default for that key rather than dropping it.

    Defensive ``try/except`` around the int coercion: a
    misconfigured value (string ``"thirty"``) silently
    falls back rather than crashing the plugin at boot.

    Args:
        config: live ``ConfigManager``, or ``None`` for a
            pure-defaults read (used in test/script
            contexts).

    Returns:
        Copy of the timeout table with all five keys
        populated.
    """
    if config is None:
        return dict(DEFAULT_TIMEOUTS)
    out: dict[str, int] = {}
    for key, default in DEFAULT_TIMEOUTS.items():
        try:
            out[key] = int(
                config.get(f"cli_timeouts.{key}", default),
            )
        except (TypeError, ValueError):
            out[key] = default
    return out
