"""core/paths.py — Plugin root resolution.

# OP-04f | core/paths.py | Depends: (none)

Single source of truth for locating the Unifideck install
directory on disk. Replaces three hand-rolled heuristics that
used to drift in symlinked/dev layouts.
"""
from __future__ import annotations

from pathlib import Path


def resolve_plugin_dir(start: Path | None = None) -> Path:
    """Locate the plugin root by trying, in order: ``UNIFIDECK_PLUGIN_DIR``
    env, ``DECKY_PLUGIN_DIR`` env, ``~/homebrew/plugins/unifideck``,
    then walking up from ``start`` looking for ``plugin.json``.
    Never raises — falls back to the canonical Decky path on failure.
    """
    raise NotImplementedError("OP-04f: implement env + walk-up resolution")


def resolve_py_modules_dir() -> Path:
    """Return ``<plugin_root>/py_modules``.
    Used by the launcher shim to bootstrap ``sys.path`` before
    importing any ``unifideck.*`` module.
    """
    raise NotImplementedError("OP-04f: resolve_plugin_dir() / 'py_modules'")
