"""core/paths.py — Plugin root resolution.

# OP-04f | core/paths.py | Depends: (none)

Single source of truth for locating the Unifideck install
directory on disk. Replaces three hand-rolled heuristics that
used to drift in symlinked/dev layouts.
"""
from __future__ import annotations

import os
from pathlib import Path

# Canonical fallback when no env var or walk-up succeeds.
_CANONICAL = Path.home() / "homebrew" / "plugins" / "unifideck"


def resolve_plugin_dir(start: Path | None = None) -> Path:
    """Locate the plugin root by trying, in order: ``UNIFIDECK_PLUGIN_DIR``
    env, ``DECKY_PLUGIN_DIR`` env, ``~/homebrew/plugins/unifideck``,
    then walking up from ``start`` looking for ``plugin.json``.
    Never raises — falls back to the canonical Decky path on failure.
    """
    # Priority 1: explicit env var
    for env_key in ("UNIFIDECK_PLUGIN_DIR", "DECKY_PLUGIN_DIR"):
        val = os.environ.get(env_key)
        if val:
            p = Path(val)
            if p.is_dir():
                return p

    # Priority 2: canonical Decky path
    if _CANONICAL.is_dir():
        return _CANONICAL

    # Priority 3: walk up from start looking for plugin.json
    if start is not None:
        current = Path(start).resolve()
        for _ in range(10):  # max 10 levels up
            if (current / "plugin.json").is_file():
                return current
            parent = current.parent
            if parent == current:
                break
            current = parent

    # Fallback: canonical path even if it doesn't exist yet
    return _CANONICAL


def resolve_py_modules_dir() -> Path:
    """Return ``<plugin_root>/py_modules``.
    Used by the launcher shim to bootstrap ``sys.path`` before
    importing any ``unifideck.*`` module.
    """
    return resolve_plugin_dir() / "py_modules"
